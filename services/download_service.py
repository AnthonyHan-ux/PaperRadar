"""Robust PDF downloader.

``requests.get(url).content`` is not a downloader.  This module implements:

* candidate fallback (try every legal URL until one validates)
* resume via HTTP Range when a ``.part`` file survives an interruption
* retries with exponential backoff on timeouts/5xx
* explicit handling of 401/403 (blocked, *not* "not found")
* streaming to ``tmp/<hash>.part`` and an atomic rename only after validation
* size limits, cancellation and progress callbacks

A download only becomes ``VALIDATED`` after :class:`PdfValidator` accepts it.
HTML error pages are detected before they can pollute the library.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import httpx

from models.paper import PdfCandidate, PdfStatus, Paper
from services.library_service import LibraryService
from services.pdf_validator import PdfValidator, ValidationResult
from utils.config import Settings
from utils.filename import human_size, long_path
from utils.rate_limiter import RateLimiter

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]
CHUNK_SIZE = 256 * 1024


@dataclass
class DownloadAttempt:
    url: str
    provider: str
    ok: bool = False
    http_status: Optional[int] = None
    bytes_downloaded: int = 0
    reason: str = ""
    elapsed: float = 0.0
    validation_status: str = ""

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "provider": self.provider,
            "ok": self.ok,
            "http_status": self.http_status,
            "bytes": self.bytes_downloaded,
            "reason": self.reason,
            "elapsed": round(self.elapsed, 2),
            "validation": self.validation_status,
        }


@dataclass
class DownloadResult:
    title: str
    status: PdfStatus
    path: Optional[Path] = None
    used_url: str = ""
    provider: str = ""
    attempts: list[DownloadAttempt] = field(default_factory=list)
    validation: Optional[ValidationResult] = None
    message: str = ""
    paper: Optional[Paper] = None

    @property
    def ok(self) -> bool:
        return self.status in (PdfStatus.VALIDATED, PdfStatus.TITLE_WARNING)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "status": self.status.value,
            "path": str(self.path) if self.path else "",
            "url": self.used_url,
            "provider": self.provider,
            "message": self.message,
            "attempts": [a.as_dict() for a in self.attempts],
        }


class _Cancelled(Exception):
    pass


class DownloadService:
    def __init__(
        self,
        settings: Settings,
        validator: Optional[PdfValidator] = None,
        library: Optional[LibraryService] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.settings = settings
        cfg = settings.download_cfg
        self.timeout = float(cfg.get("request_timeout_seconds", 90))
        self.min_bytes = int(cfg.get("min_pdf_bytes", 10_000))
        self.max_bytes = int(cfg.get("max_pdf_bytes", 250 * 1024 * 1024))
        self.max_retries = int(settings.api.get("max_retries", 3))
        self.resume_enabled = bool(cfg.get("enable_pdf_resume", True))
        self.validator = validator or PdfValidator(
            min_bytes=self.min_bytes,
            title_threshold=float(cfg.get("validate_title_threshold", 90)),
            warning_threshold=float(cfg.get("validate_title_warning", 80)),
        )
        self.library = library or LibraryService(settings)
        self.tmp_dir = settings.paths.tmp_dir
        self.bucket = "download"
        self.limiter = RateLimiter(
            default_interval=0.0,
            intervals={self.bucket: 0.4},
        )
        self._client_lock = threading.Lock()
        self._client: Optional[httpx.Client] = None
        self.headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if headers:
            self.headers.update(headers)
        try:
            self.tmp_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    @property
    def client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.Client(
                    timeout=httpx.Timeout(self.timeout, connect=20.0),
                    follow_redirects=True,
                    headers=self.headers,
                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                )
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                self._client.close()
            self._client = None

    # ------------------------------------------------------------------ #
    def download(
        self,
        paper: Paper,
        candidates: Optional[Iterable[PdfCandidate]] = None,
        cancel_event: Optional[threading.Event] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> DownloadResult:
        cand_list = list(candidates if candidates is not None else paper.pdf_candidates)
        cand_list = [c for c in cand_list if c and c.url]

        if not cand_list:
            return DownloadResult(
                title=paper.title,
                status=PdfStatus.NO_PUBLIC_COPY_FOUND,
                message="没有找到任何合法的公开 PDF 链接",
                paper=paper,
            )

        attempts: list[DownloadAttempt] = []
        blocked = False
        paywalled = False
        last_validation: Optional[ValidationResult] = None

        for candidate in cand_list:
            if cancel_event is not None and cancel_event.is_set():
                return DownloadResult(
                    title=paper.title, status=PdfStatus.DOWNLOAD_FAILED,
                    message="用户取消", attempts=attempts, paper=paper,
                )
            outcome = self._attempt(paper, candidate, cancel_event, progress)
            attempts.append(outcome.attempt)
            if outcome.attempt.http_status in (401, 403, 402):
                blocked = True
            if outcome.attempt.http_status == 451:
                paywalled = True
            if outcome.result is not None:
                result = outcome.result
                result.attempts = attempts
                if result.ok:
                    paper.local_pdf_path = str(result.path) if result.path else None
                    paper.pdf_status = result.status
                    paper.downloaded_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                    log.info(
                        "PDF %s: %s (%s, %s)",
                        result.status.value, paper.title[:70],
                        result.provider, human_size(outcome.attempt.bytes_downloaded),
                    )
                return result
            last_validation = outcome.validation or last_validation

        if blocked:
            status, message = (
                PdfStatus.PUBLIC_BUT_BLOCKED,
                "找到公开链接，但服务器拒绝自动下载（403/401），请在浏览器中手动打开",
            )
        elif paywalled:
            status, message = (
                PdfStatus.PAYWALLED_ONLY,
                "出版社返回 451（法律/地区限制），只有付费版本可用",
            )
        elif last_validation is not None and last_validation.status == PdfStatus.WRONG_PAPER:
            status, message = (
                PdfStatus.WRONG_PAPER,
                f"所有候选链接下载到的都不是目标论文：{last_validation.reason}",
            )
        elif last_validation is not None and last_validation.status == PdfStatus.INVALID_PDF:
            status, message = (
                PdfStatus.INVALID_PDF,
                f"所有候选链接都不是有效 PDF：{last_validation.reason}",
            )
        else:
            status, message = (
                PdfStatus.DOWNLOAD_FAILED,
                "所有公开链接均下载失败（网络错误或文件不可用）",
            )

        return DownloadResult(
            title=paper.title, status=status, attempts=attempts,
            validation=last_validation, message=message, paper=paper,
        )

    # ------------------------------------------------------------------ #
    def _attempt(
        self,
        paper: Paper,
        candidate: PdfCandidate,
        cancel_event: Optional[threading.Event],
        progress: Optional[ProgressCallback],
    ) -> "_AttemptOutcome":
        url = candidate.url.strip()
        started = time.monotonic()
        attempt = DownloadAttempt(url=url, provider=candidate.provider)

        if not url.lower().startswith(("http://", "https://")):
            attempt.reason = "非 HTTP(S) 链接"
            return _AttemptOutcome(attempt=attempt)

        key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        part_path = self.tmp_dir / f"{key}.part"
        resume_offset = 0
        if self.resume_enabled and part_path.exists():
            try:
                resume_offset = part_path.stat().st_size
            except OSError:
                resume_offset = 0
            if resume_offset == 0:
                part_path.unlink(missing_ok=True)

        downloaded = resume_offset
        response_status: Optional[int] = None
        content_type = ""

        for retry in range(self.max_retries + 1):
            if cancel_event is not None and cancel_event.is_set():
                attempt.reason = "已取消"
                attempt.elapsed = time.monotonic() - started
                return _AttemptOutcome(attempt=attempt)

            headers = dict(self.headers)
            if resume_offset > 0:
                headers["Range"] = f"bytes={resume_offset}-"

            try:
                self.limiter.wait(self.bucket)
                with self.client.stream("GET", url, headers=headers) as response:
                    response_status = response.status_code
                    content_type = response.headers.get("content-type", "").lower()
                    attempt.http_status = response_status

                    if response_status in (401, 403, 402):
                        attempt.reason = f"HTTP {response_status} 拒绝访问（反爬/付费墙）"
                        attempt.elapsed = time.monotonic() - started
                        return _AttemptOutcome(attempt=attempt)
                    if response_status in (404, 410):
                        attempt.reason = f"HTTP {response_status} 链接不存在"
                        attempt.elapsed = time.monotonic() - started
                        return _AttemptOutcome(attempt=attempt)
                    if response_status in (429, 500, 502, 503, 504):
                        if retry < self.max_retries:
                            self.limiter.sleep_backoff(retry)
                            continue
                        attempt.reason = f"HTTP {response_status} 服务端错误"
                        attempt.elapsed = time.monotonic() - started
                        return _AttemptOutcome(attempt=attempt)
                    if response_status == 416:
                        # Stale partial file: restart from zero once.
                        part_path.unlink(missing_ok=True)
                        resume_offset = 0
                        downloaded = 0
                        if retry < self.max_retries:
                            continue
                        attempt.reason = "HTTP 416 无法续传"
                        attempt.elapsed = time.monotonic() - started
                        return _AttemptOutcome(attempt=attempt)
                    if response_status >= 400:
                        attempt.reason = f"HTTP {response_status}"
                        attempt.elapsed = time.monotonic() - started
                        return _AttemptOutcome(attempt=attempt)

                    # 206 means the server honoured our Range header.
                    if response_status == 206 and resume_offset > 0:
                        mode = "ab"
                    else:
                        mode = "wb"
                        if response_status == 200:
                            resume_offset = 0
                            downloaded = 0

                    total = self._total_size(response, downloaded)
                    downloaded = resume_offset
                    first_chunk = b""

                    with open(long_path(part_path), mode) as handle:
                        for chunk in response.iter_bytes(CHUNK_SIZE):
                            if cancel_event is not None and cancel_event.is_set():
                                raise _Cancelled()
                            if not chunk:
                                continue
                            if not first_chunk:
                                first_chunk = chunk[:2048]
                            downloaded += len(chunk)
                            if downloaded > self.max_bytes:
                                raise ValueError(
                                    f"文件超过上限 {human_size(self.max_bytes)}"
                                )
                            handle.write(chunk)
                            if progress is not None:
                                try:
                                    progress(paper.title, downloaded, total)
                                except Exception:  # progress must never break a download
                                    pass

                    attempt.bytes_downloaded = downloaded

                    # Cheap rejection of HTML interstitials before validation.
                    if (
                        "text/html" in content_type
                        and b"%PDF-" not in first_chunk
                    ):
                        part_path.unlink(missing_ok=True)
                        attempt.reason = "服务器返回 HTML 页面（登录墙/反爬/错误页）"
                        attempt.elapsed = time.monotonic() - started
                        return _AttemptOutcome(attempt=attempt)

                    break  # streaming finished, go validate

            except _Cancelled:
                attempt.reason = "已取消"
                attempt.elapsed = time.monotonic() - started
                return _AttemptOutcome(attempt=attempt)
            except ValueError as exc:  # size limit
                part_path.unlink(missing_ok=True)
                attempt.reason = str(exc)
                attempt.elapsed = time.monotonic() - started
                return _AttemptOutcome(attempt=attempt)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError, OSError) as exc:
                attempt.reason = f"{type(exc).__name__}: {exc}"
                if retry < self.max_retries:
                    log.debug("download retry %d for %s: %s", retry + 1, url, exc)
                    self.limiter.sleep_backoff(retry)
                    try:
                        resume_offset = part_path.stat().st_size
                        downloaded = resume_offset
                    except OSError:
                        resume_offset = 0
                    continue
                attempt.elapsed = time.monotonic() - started
                return _AttemptOutcome(attempt=attempt)

        if not part_path.exists() or part_path.stat().st_size == 0:
            attempt.reason = attempt.reason or "下载为空文件"
            attempt.elapsed = time.monotonic() - started
            return _AttemptOutcome(attempt=attempt)

        if progress is not None:
            try:
                progress(paper.title, downloaded, downloaded)
            except Exception:
                pass

        validation = self.validator.validate(part_path, paper)
        attempt.validation_status = validation.status.value
        attempt.elapsed = time.monotonic() - started

        if validation.ok:
            destination = self.library.reserve_destination(paper)
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                Path(long_path(part_path)).replace(Path(long_path(destination)))
            except OSError as exc:
                attempt.reason = f"无法移动到文献库：{exc}"
                return _AttemptOutcome(attempt=attempt, validation=validation)
            attempt.ok = True
            attempt.bytes_downloaded = downloaded
            result = DownloadResult(
                title=paper.title,
                status=validation.status,
                path=destination,
                used_url=url,
                provider=candidate.provider,
                validation=validation,
                message=validation.reason,
                paper=paper,
            )
            return _AttemptOutcome(attempt=attempt, result=result, validation=validation)

        # Invalid/wrong file: never leave it behind.
        part_path.unlink(missing_ok=True)
        attempt.reason = validation.reason
        attempt.bytes_downloaded = downloaded
        return _AttemptOutcome(attempt=attempt, validation=validation)

    def _total_size(self, response: httpx.Response, offset: int) -> int:
        raw = response.headers.get("content-length")
        try:
            length = int(raw) if raw else 0
        except ValueError:
            length = 0
        if response.status_code == 206:
            return length + offset
        return length

    # ------------------------------------------------------------------ #
    def download_many(
        self,
        papers: list[Paper],
        max_workers: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
        progress: Optional[ProgressCallback] = None,
        on_result: Optional[Callable[[DownloadResult], None]] = None,
    ) -> list[DownloadResult]:
        if not papers:
            return []
        cfg_workers = int(self.settings.download_cfg.get("max_workers", 3))
        workers = max(1, min(max_workers or cfg_workers, 4, len(papers)))
        results: dict[int, DownloadResult] = {}

        def task(index: int, paper: Paper) -> tuple[int, DownloadResult]:
            return index, self.download(paper, cancel_event=cancel_event, progress=progress)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dl") as pool:
            futures = {pool.submit(task, i, paper): i for i, paper in enumerate(papers)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    idx, result = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    result = DownloadResult(
                        title=papers[index].title,
                        status=PdfStatus.DOWNLOAD_FAILED,
                        message=f"下载线程异常：{exc}",
                    )
                    idx = index
                results[idx] = result
                if on_result is not None:
                    try:
                        on_result(result)
                    except Exception:
                        pass

        return [results.get(i) or DownloadResult(
            title=papers[i].title, status=PdfStatus.DOWNLOAD_FAILED,
            message="未返回结果",
        ) for i in range(len(papers))]


@dataclass
class _AttemptOutcome:
    attempt: DownloadAttempt
    result: Optional[DownloadResult] = None
    validation: Optional[ValidationResult] = None
