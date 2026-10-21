"""Application service: the single object the GUI and the CLI both talk to.

Wires configuration, HTTP, providers, database, OA resolution, downloads and
export together, and owns the cross-cutting rules (what gets persisted when,
which status wins, when a record is marked as "already in library").
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from database.db import Database, DbStats
from models.paper import OaStatus, PdfStatus, Paper, SearchMode
from providers.base import SearchQuery
from providers.http import HttpClient
from services.download_service import DownloadResult, DownloadService, ProgressCallback
from services.export_service import ExportService
from services.library_service import LibraryService
from services.oa_finder import OAFinder, OAFinding
from services.pdf_validator import PdfValidator
from services.relevance_service import RelevanceService
from services.search_service import SearchOutcome, SearchService, build_providers
from utils.config import AppPaths, Settings, load_settings
from utils.logger import setup_logging
from utils.rate_limiter import JsonCache, RateLimiter

log = logging.getLogger(__name__)


class AppService:
    def __init__(self, settings: Optional[Settings] = None,
                 db: Optional[Database] = None) -> None:
        self.settings = settings or load_settings()
        self.paths: AppPaths = self.settings.paths.ensure()

        self.log_handler = setup_logging(
            self.paths.logs_dir,
            level=str(self.settings.app_cfg.get("log_level", "INFO")),
        )

        # Surface a V1.0 -> V1.1 data migration in the GUI log / CLI output.
        import utils.config as config_module

        self.migration_report = getattr(config_module, "LAST_MIGRATION", None)
        if self.migration_report is not None and getattr(
            self.migration_report, "attempted", False
        ):
            log.info(self.migration_report.summary())

        self.http = HttpClient(
            user_agent=self.settings.user_agent,
            timeout=float(self.settings.api.get("timeout_seconds", 30)),
            max_retries=int(self.settings.api.get("max_retries", 3)),
            limiter=RateLimiter(intervals=self.settings.rate_limits),
            cache=JsonCache(self.paths.cache_dir, ttl_seconds=self.settings.cache_ttl_seconds),
        )
        self.db = db or Database(self.paths.db_path)
        if getattr(self.db, "corruption_note", ""):
            log.critical(self.db.corruption_note)
        self._cleanup_stale_temp_files()
        self.providers = build_providers(self.settings, self.http)
        self.relevance = RelevanceService(self.settings)
        self.search_service = SearchService(
            self.settings, self.http, self.providers, self.relevance
        )
        self.oa_finder = OAFinder(self.settings, self.providers)
        self.library = LibraryService(self.settings)
        self.validator = PdfValidator(
            min_bytes=int(self.settings.download_cfg.get("min_pdf_bytes", 10_000)),
            title_threshold=float(self.settings.download_cfg.get("validate_title_threshold", 90)),
            warning_threshold=float(self.settings.download_cfg.get("validate_title_warning", 80)),
        )
        self.downloader = DownloadService(
            self.settings, validator=self.validator, library=self.library
        )
        self.exporter = ExportService()
        self._closed = False
        log.info(
            "PaperRadar ready | providers=%s | db=%s",
            ",".join(self.settings.enabled_providers), self.paths.db_path,
        )

    # ------------------------------------------------------------------ #
    def _cleanup_stale_temp_files(self, max_age_hours: float = 24.0) -> int:
        """Remove ``*.part`` leftovers from interrupted downloads.

        Only files older than ``max_age_hours`` are touched, so a download that
        is running right now is never disturbed.
        """
        removed = 0
        cutoff = time.time() - max_age_hours * 3600
        try:
            candidates = list(self.paths.tmp_dir.glob("*.part"))
        except OSError:  # pragma: no cover
            return 0
        for path in candidates:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        if removed:
            log.info("removed %d stale partial downloads", removed)
        return removed

    # ------------------------------------------------------------------ #
    def clear_cache(self) -> int:
        """Delete cached API responses only.  Never touches DB or library."""
        removed = 0
        try:
            for path in self.paths.cache_dir.glob("*.json"):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        except OSError:  # pragma: no cover
            pass
        log.info("cache cleared (%d files)", removed)
        return removed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.http.close()
        except Exception:
            pass
        try:
            self.downloader.close()
        except Exception:
            pass
        try:
            self.db.close_all()
        except Exception:
            pass
        log.info("PaperRadar closed")

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #
    def run_search(self, query: SearchQuery, mark_existing: bool = True) -> SearchOutcome:
        try:
            outcome = self.search_service.search(query)
            if mark_existing:
                self.mark_existing(outcome.papers)
            try:
                self.db.add_search_history(
                    query.text, query.mode.value,
                    {
                        "year_from": query.year_from,
                        "year_to": query.year_to,
                        'date_from':query.date_from,
                        'date_to':query.date_to,
                        "venues": query.venues,
                        'directions':query.extra.get('directions',[]),
                        'match_mode':query.extra.get('match_mode'),
                        'include_uncertain_dates':bool(query.extra.get('include_uncertain_dates')),
                    },
                    len(outcome.papers),
                )
            except Exception as exc:  # pragma: no cover - history is optional
                log.debug("search history failed: %s", exc)
            return outcome
        finally:
            # The GUI runs this on a QThreadPool thread; close its connection so
            # the database file is never held by a pooled thread.
            self.db.close_thread()

    def search_profile(self, limit: Optional[int] = None,
                       year_from: Optional[int] = None,
                       year_to: Optional[int] = None) -> SearchOutcome:
        terms = [t for t in self.settings.profile.get("keywords", []) if t][:4]
        text = " ".join(terms) or str(self.settings.profile.get("name") or "")
        query = SearchQuery(
            text=text,
            mode=SearchMode.KEYWORDS,
            year_from=year_from,
            year_to=year_to,
            limit=limit or int(self.settings.search_cfg.get("default_limit", 30)),
        )
        return self.run_search(query)

    def mark_existing(self, papers: Iterable[Paper]) -> None:
        """Annotate search results with local-library state."""
        for paper in papers:
            try:
                existing_id, _reason = self.db.find_existing(paper)
            except Exception as exc:  # pragma: no cover
                log.debug("db lookup failed: %s", exc)
                continue
            if existing_id is None:
                continue
            paper.id = existing_id
            stored = self.db.get(existing_id)
            if stored is None:
                continue
            if stored.local_pdf_path and self.library.file_exists(stored.local_pdf_path):
                paper.local_pdf_path = stored.local_pdf_path
                paper.pdf_status = stored.pdf_status
                paper.downloaded_at = stored.downloaded_at
            elif stored.local_pdf_path:
                # The row points at a file that no longer exists.  Keep the
                # record (never delete the user's metadata) and mark it.
                self.db.update_download_state(
                    existing_id, PdfStatus.MISSING_LOCAL_FILE, ""
                )
                paper.pdf_status = PdfStatus.MISSING_LOCAL_FILE
                paper.local_pdf_path = None
            if stored.oa_status != OaStatus.UNKNOWN:
                paper.oa_status = stored.oa_status
            if not paper.abstract and stored.abstract:
                paper.abstract = stored.abstract

    # ------------------------------------------------------------------ #
    # OA + download
    # ------------------------------------------------------------------ #
    def find_oa(self, paper: Paper) -> OAFinding:
        finding = self.oa_finder.find(paper)
        self._persist_paper(paper)
        return finding

    def download_one(
        self,
        paper: Paper,
        cancel_event: Optional[threading.Event] = None,
        progress: Optional[ProgressCallback] = None,
        auto_resolve: bool = True,
    ) -> DownloadResult:
        try:
            result = self._download_with_recovery(paper, cancel_event, progress, auto_resolve)
            paper.pdf_status = result.status
            self._persist_download(result, paper)
            return result
        finally:
            self.db.close_thread()

    def _download_with_recovery(
        self, paper: Paper, cancel_event: Optional[threading.Event],
        progress: Optional[ProgressCallback], auto_resolve: bool,
    ) -> DownloadResult:
        if cancel_event is not None and cancel_event.is_set():
            return DownloadResult(title=paper.title, status=PdfStatus.DOWNLOAD_FAILED,
                                  message="用户取消", paper=paper)
        existing = list(paper.pdf_candidates)
        resolved = False
        if auto_resolve and not existing:
            self.oa_finder.find(paper)
            resolved = True
        if not paper.pdf_candidates and paper.oa_status == OaStatus.CLOSED:
            return DownloadResult(title=paper.title, status=PdfStatus.PAYWALLED_ONLY,
                                  message="未发现公开副本，来源报告为非开放获取", paper=paper)
        result = self.downloader.download(
            paper, paper.pdf_candidates, cancel_event=cancel_event, progress=progress
        )
        if result.ok or not auto_resolve or resolved:
            return result
        if cancel_event is not None and cancel_event.is_set():
            return result
        # A stale URL is a failed attempt, not evidence that no public copy
        # exists. Discover alternatives once, then try only unattempted URLs.
        try:
            self.oa_finder.find(paper)
        except Exception as exc:
            result.message += f"；公开链接补查失败（{type(exc).__name__}）"
            return result
        attempted = {candidate.url for candidate in existing}
        attempted.update(attempt.url for attempt in result.attempts)
        fresh = [candidate for candidate in paper.pdf_candidates if candidate.url not in attempted]
        if not fresh:
            return result
        recovered = self.downloader.download(paper, fresh, cancel_event=cancel_event, progress=progress)
        recovered.attempts = result.attempts + recovered.attempts
        if not recovered.ok and result.status == PdfStatus.PUBLIC_BUT_BLOCKED:
            recovered.status = PdfStatus.PUBLIC_BUT_BLOCKED
        return recovered

    def download_many(
        self,
        papers: list[Paper],
        cancel_event: Optional[threading.Event] = None,
        progress: Optional[ProgressCallback] = None,
        on_result: Optional[Callable[[DownloadResult], None]] = None,
        auto_resolve: bool = True,
    ) -> list[DownloadResult]:
        # Use the same bounded recovery path as single downloads. Each worker
        # owns its database connection, and each paper produces one callback.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        if not papers:
            return []
        workers = max(1, min(4, len(papers), int(self.settings.download_cfg.get("max_workers", 3))))
        results = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="acquire") as pool:
            futures = {pool.submit(self.download_one, paper, cancel_event, progress, auto_resolve): index
                       for index, paper in enumerate(papers)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = DownloadResult(title=papers[index].title, status=PdfStatus.DOWNLOAD_FAILED,
                                            message=f"下载线程异常：{type(exc).__name__}", paper=papers[index])
                    try:
                        self._persist_download(result, papers[index])
                    finally:
                        self.db.close_thread()
                results[index] = result
                if on_result is not None:
                    try:
                        on_result(result)
                    except Exception:
                        log.exception("Download result callback failed")
        return [results[index] for index in range(len(papers))]

    # ------------------------------------------------------------------ #
    def _persist_paper(self, paper: Paper) -> Optional[int]:
        try:
            paper_id, _created, _reason = self.db.upsert(paper)
            paper.id = paper_id
            return paper_id
        except Exception as exc:
            log.warning("cannot persist paper %r: %s", paper.title[:60], exc)
            return paper.id

    def _persist_download(self, result: DownloadResult, paper: Optional[Paper]) -> None:
        paper = paper or result.paper
        if paper is None:
            return
        if paper.id is None:
            self._persist_paper(paper)
        if paper.id is None:
            return
        try:
            attempts = getattr(result, "attempts", None) or []
            http_status = None
            for attempt in attempts:
                if attempt.url == result.used_url:
                    http_status = attempt.http_status
                    break
            if http_status is None and attempts:
                http_status = attempts[-1].http_status

            self.db.update_download_state(
                paper.id, result.status, paper.local_pdf_path, paper.oa_status
            )
            self.db.log_download(
                paper,
                status=result.status.value,
                url=result.used_url,
                provider=result.provider,
                reason=result.message,
                http_status=http_status,
            )
            if result.ok and result.path:
                paper.local_pdf_path = str(result.path)
        except Exception as exc:  # pragma: no cover
            log.warning("cannot persist download state: %s", exc)

    # ------------------------------------------------------------------ #
    # Library / export
    # ------------------------------------------------------------------ #
    def add_to_library(self, paper: Paper) -> tuple[int, bool]:
        paper_id, created, _reason = self.db.upsert(paper)
        paper.id = paper_id
        return paper_id, created

    def add_many(self, papers: Iterable[Paper]) -> tuple[int, int]:
        added = 0
        existing = 0
        for paper in papers:
            try:
                _pid, created = self.add_to_library(paper)
                if created:
                    added += 1
                else:
                    existing += 1
            except Exception as exc:  # pragma: no cover
                log.warning("cannot add %r: %s", paper.title[:60], exc)
        return added, existing

    def library_papers(self, limit: int = 500, only_local: bool = True,
                       search: str = "") -> list[Paper]:
        return self.db.list_papers(limit=limit, only_local=only_local, search=search)

    def stats(self) -> DbStats:
        return self.db.stats()

    def export_papers(
        self, papers: Iterable[Paper], fmt: str, path: Optional[Path] = None
    ) -> Path:
        fmt = (fmt or "csv").lower()
        target = Path(path) if path else (
            self.paths.export_dir / self.exporter.default_filename(fmt)
        )
        return self.exporter.export(papers, fmt, target)

    # ------------------------------------------------------------------ #
    def reload_profile(self) -> None:
        self.settings = load_settings(home=self.paths.home)
        self.relevance = RelevanceService(self.settings)
        self.search_service.relevance = self.relevance

    def save_profile(self, profile: dict) -> None:
        self.settings.save_profile(profile)
        self.relevance = RelevanceService(self.settings)
        self.search_service.relevance = self.relevance
