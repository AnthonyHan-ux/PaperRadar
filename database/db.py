"""SQLite persistence.

Design notes:

* WAL mode + ``busy_timeout`` so GUI thread writes and download workers coexist.
* One connection per thread (``sqlite3.Connection`` is not thread-safe); the
  ``Database`` object is therefore safe to share across QThreadPool workers.
* Duplicate prevention is done in :meth:`find_existing` with the documented
  priority (DOI -> arXiv -> exact normalised title -> fuzzy title) and enforced
  by unique indexes as a second line of defence.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from models.paper import OaStatus, PdfCandidate, PdfStatus, Paper
from utils.title_normalizer import normalize_title, title_similarity

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load_json(raw: Any, default: Any) -> Any:
    if not raw:
        return default
    try:
        value = json.loads(raw)
        return value if isinstance(value, type(default)) else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


@dataclass
class DbStats:
    total: int = 0
    with_pdf: int = 0
    validated: int = 0
    no_public_copy: int = 0
    paywalled: int = 0
    blocked: int = 0
    by_status: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.by_status is None:
            self.by_status = {}


class Database:
    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        # Every connection ever opened, so that shutdown can release Windows
        # file handles held by QThreadPool worker threads.
        self._conn_lock = threading.Lock()
        self._connections: set[sqlite3.Connection] = set()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    # ------------------------------------------------------------------ #
    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # check_same_thread=False is required so shutdown (main thread) can
            # close connections opened by QThreadPool workers; each connection
            # is still used by exactly one thread at a time.
            conn = sqlite3.connect(str(self.path), timeout=20.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 8000")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
            with self._conn_lock:
                self._connections.add(conn)
        return conn

    def close_thread(self) -> None:
        """Close the calling thread's connection (worker threads call this)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._conn_lock:
            self._connections.discard(conn)
        try:
            conn.close()
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            log.debug("close_thread failed: %s", exc)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("close_thread failed: %s", exc)

    # Backwards-compatible name used by older call sites.
    def close(self) -> None:
        self.close_thread()

    def close_all(self) -> None:
        """Close every connection opened by any thread.

        Called on application shutdown; without this, worker-thread connections
        keep ``paperradar.db`` locked on Windows, which breaks backup/migration.
        """
        self.close_thread()
        with self._conn_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            try:
                conn.close()
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("close_all failed for a connection: %s", exc)

    def _initialise(self) -> None:
        conn = self.connection
        conn.execute("PRAGMA journal_mode = WAL")
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        with self._write_lock:
            conn.executescript(schema)
            conn.commit()
        log.info("database ready: %s", self.path)

    def _execute(self, sql: str, params: Iterable[Any] = (), *, commit: bool = False,
                 retries: int = 5) -> sqlite3.Cursor:
        last_error: Optional[sqlite3.Error] = None
        for attempt in range(retries):
            try:
                cursor = self.connection.execute(sql, tuple(params))
                if commit:
                    with self._write_lock:
                        self.connection.commit()
                return cursor
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "locked" in str(exc).lower() and attempt < retries - 1:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise
        raise last_error  # type: ignore[misc]

    # ------------------------------------------------------------------ #
    # Row <-> Paper
    # ------------------------------------------------------------------ #
    @staticmethod
    def row_to_paper(row: sqlite3.Row) -> Paper:
        candidates = [
            PdfCandidate(
                url=item.get("url", ""),
                provider=item.get("provider", ""),
                is_oa=bool(item.get("is_oa", True)),
                note=item.get("note", ""),
            )
            for item in _load_json(row["pdf_urls"], [])
            if isinstance(item, dict) and item.get("url")
        ]
        paper = Paper(
            title=row["title"],
            authors=_load_json(row["authors"], []),
            year=row["year"],
            venue=row["venue"],
            doi=row["doi"],
            arxiv_id=row["arxiv_id"],
            abstract=row["abstract"],
            keywords=_load_json(row["keywords"], []),
            url=row["source_url"],
            source_url=row["source_url"],
            oa_status=OaStatus(row["oa_status"] or "UNKNOWN"),
            pdf_status=PdfStatus(row["pdf_status"] or "NOT_CHECKED"),
            sources=_load_json(row["sources"], []),
            citation_count=row["citation_count"],
            local_pdf_path=row["local_path"],
            relevance_score=row["relevance_score"],
            id=row["id"],
            discovered_at=row["discovered_at"],
            downloaded_at=row["downloaded_at"],
        )
        paper.pdf_candidates = candidates
        return paper

    def _paper_params(self, paper: Paper) -> tuple:
        return (
            paper.title,
            normalize_title(paper.title),
            _json(paper.authors),
            paper.year,
            paper.venue,
            paper.doi,
            paper.arxiv_id,
            paper.abstract,
            _json(paper.keywords),
            paper.oa_status.value,
            paper.pdf_status.value,
            paper.local_pdf_path,
            paper.source_url or paper.url,
            _json([
                {"url": c.url, "provider": c.provider, "is_oa": c.is_oa, "note": c.note}
                for c in paper.pdf_candidates
            ]),
            _json(paper.sources),
            paper.citation_count,
            paper.relevance_score,
        )

    # ------------------------------------------------------------------ #
    # Lookup / dedupe
    # ------------------------------------------------------------------ #
    def find_existing(
        self, paper: Paper, fuzzy_threshold: float = 95.0
    ) -> tuple[Optional[int], str]:
        """Return ``(id, reason)`` for an existing record, or ``(None, "")``."""
        if paper.doi:
            row = self._execute("SELECT id FROM papers WHERE doi = ?", (paper.doi,)).fetchone()
            if row:
                return int(row["id"]), "doi"
        if paper.arxiv_id:
            row = self._execute(
                "SELECT id FROM papers WHERE arxiv_id = ?", (paper.arxiv_id,)
            ).fetchone()
            if row:
                return int(row["id"]), "arxiv"

        normalized = normalize_title(paper.title)
        if not normalized:
            return None, ""
        row = self._execute(
            "SELECT id FROM papers WHERE normalized_title = ?", (normalized,)
        ).fetchone()
        if row:
            return int(row["id"]), "title"

        # Fuzzy: prefer records from the same/adjacent year, cap the scan.
        year = paper.year
        if year is not None:
            rows = self._execute(
                "SELECT id, title FROM papers WHERE year BETWEEN ? AND ? LIMIT 800",
                (int(year) - 1, int(year) + 1),
            ).fetchall()
        else:
            rows = self._execute("SELECT id, title FROM papers LIMIT 800").fetchall()

        best_id: Optional[int] = None
        best_score = 0.0
        for row in rows:
            score = title_similarity(paper.title, row["title"])
            if score > best_score:
                best_score = score
                best_id = int(row["id"])
        if best_id is not None and best_score >= fuzzy_threshold:
            return best_id, f"fuzzy:{best_score:.1f}"
        return None, ""

    def get(self, paper_id: int) -> Optional[Paper]:
        row = self._execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        return self.row_to_paper(row) if row else None

    def get_by_doi(self, doi: str) -> Optional[Paper]:
        row = self._execute("SELECT * FROM papers WHERE doi = ?", (doi,)).fetchone()
        return self.row_to_paper(row) if row else None

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #
    def upsert(self, paper: Paper, fuzzy_threshold: float = 95.0) -> tuple[int, bool, str]:
        """Insert or merge ``paper``; returns ``(id, created, reason)``."""
        existing_id, reason = self.find_existing(paper, fuzzy_threshold)
        if existing_id is not None:
            existing = self.get(existing_id)
            if existing is not None:
                existing.merge_from(paper)
                existing.id = existing_id
                self.update(existing)
            self._record_sources(existing_id, paper)
            return existing_id, False, reason

        paper_id = self.insert(paper)
        self._record_sources(paper_id, paper)
        return paper_id, True, "new"

    def insert(self, paper: Paper) -> int:
        now = _utc_now()
        values = self._paper_params(paper) + (
            paper.discovered_at or now,  # discovered_at
            now,                         # updated_at
            paper.downloaded_at,         # downloaded_at
        )
        sql = """
            INSERT INTO papers (
                title, normalized_title, authors, year, venue, doi, arxiv_id,
                abstract, keywords, oa_status, pdf_status, local_path,
                source_url, pdf_urls, sources, citation_count, relevance_score,
                discovered_at, updated_at, downloaded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        cursor = self._execute(sql, values, commit=True)
        paper.id = int(cursor.lastrowid or 0)
        log.debug("inserted paper #%s: %s", paper.id, paper.title[:60])
        return paper.id

    def update(self, paper: Paper) -> None:
        if paper.id is None:
            raise ValueError("paper.id is required for update()")
        params = self._paper_params(paper) + (
            _utc_now(), paper.downloaded_at, paper.id,
        )
        sql = """
            UPDATE papers SET
                title = ?, normalized_title = ?, authors = ?, year = ?, venue = ?,
                doi = ?, arxiv_id = ?, abstract = ?, keywords = ?, oa_status = ?,
                pdf_status = ?, local_path = ?, source_url = ?, pdf_urls = ?,
                sources = ?, citation_count = ?, relevance_score = ?,
                updated_at = ?, downloaded_at = ?
            WHERE id = ?
        """
        self._execute(sql, params, commit=True)

    def update_download_state(
        self,
        paper_id: int,
        pdf_status: PdfStatus,
        local_path: Optional[str] = None,
        oa_status: Optional[OaStatus] = None,
    ) -> None:
        now = _utc_now()
        sets = ["pdf_status = ?", "updated_at = ?", "checked_at = ?"]
        params: list[Any] = [pdf_status.value, now, now]
        if local_path is not None:
            sets.append("local_path = ?")
            params.append(local_path[:4000])
            if pdf_status.has_local_file:
                sets.append("downloaded_at = ?")
                params.append(now)
        if oa_status is not None:
            sets.append("oa_status = ?")
            params.append(oa_status.value)
        params.append(paper_id)
        self._execute(f"UPDATE papers SET {', '.join(sets)} WHERE id = ?",
                      params, commit=True)

    def _record_sources(self, paper_id: int, paper: Paper) -> None:
        if not paper.sources:
            return
        now = _utc_now()
        for provider in paper.sources:
            try:
                self._execute(
                    "INSERT OR IGNORE INTO paper_sources (paper_id, provider, source_url, discovered_at)"
                    " VALUES (?,?,?,?)",
                    (paper_id, provider, paper.source_url, now),
                    commit=True,
                )
            except sqlite3.Error as exc:  # pragma: no cover - defensive
                log.debug("cannot record source %s: %s", provider, exc)

    def set_notes(self, paper_id: int, notes: str) -> None:
        self._execute(
            "UPDATE papers SET notes = ?, updated_at = ? WHERE id = ?",
            (notes, _utc_now(), paper_id), commit=True,
        )

    def delete(self, paper_id: int) -> None:
        self._execute("DELETE FROM papers WHERE id = ?", (paper_id,), commit=True)

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def list_papers(
        self,
        *,
        only_local: bool = False,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "discovered_at DESC",
        search: str = "",
    ) -> list[Paper]:
        clauses: list[str] = []
        params: list[Any] = []
        if only_local:
            clauses.append("local_path IS NOT NULL AND local_path <> ''")
        if search:
            clauses.append("(title LIKE ? OR abstract LIKE ? OR authors LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        allowed_order = {
            "discovered_at DESC", "year DESC", "relevance_score DESC", "title ASC",
        }
        if order_by not in allowed_order:
            order_by = "discovered_at DESC"
        rows = self._execute(
            f"SELECT * FROM papers {where} ORDER BY {order_by} LIMIT ? OFFSET ?",
            params + [max(1, limit), max(0, offset)],
        ).fetchall()
        return [self.row_to_paper(row) for row in rows]

    def count(self, only_local: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM papers"
        if only_local:
            sql += " WHERE local_path IS NOT NULL AND local_path <> ''"
        row = self._execute(sql).fetchone()
        return int(row["n"]) if row else 0

    def stats(self) -> DbStats:
        rows = self._execute(
            "SELECT pdf_status, COUNT(*) AS n FROM papers GROUP BY pdf_status"
        ).fetchall()
        by_status = {row["pdf_status"]: int(row["n"]) for row in rows}
        stats = DbStats(total=sum(by_status.values()), by_status=by_status)
        stats.with_pdf = sum(
            n for status, n in by_status.items()
            if status in (PdfStatus.VALIDATED.value, PdfStatus.TITLE_WARNING.value,
                          PdfStatus.DOWNLOADED.value)
        )
        stats.validated = by_status.get(PdfStatus.VALIDATED.value, 0)
        stats.no_public_copy = by_status.get(PdfStatus.NO_PUBLIC_COPY_FOUND.value, 0)
        stats.paywalled = by_status.get(PdfStatus.PAYWALLED_ONLY.value, 0)
        stats.blocked = by_status.get(PdfStatus.PUBLIC_BUT_BLOCKED.value, 0)
        return stats

    def recent_downloads(self, limit: int = 100) -> list[sqlite3.Row]:
        return self._execute(
            "SELECT * FROM download_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def log_download(
        self,
        paper: Paper,
        status: str,
        url: str = "",
        provider: str = "",
        reason: str = "",
    ) -> None:
        try:
            self._execute(
                "INSERT INTO download_logs (paper_id, title, url, provider, oa_status, status, reason, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (paper.id, paper.title[:500], url[:2000], provider,
                 paper.oa_status.value, status, reason[:1000], _utc_now()),
                commit=True,
            )
        except sqlite3.Error as exc:  # pragma: no cover
            log.debug("cannot log download: %s", exc)

    def add_search_history(
        self, query: str, mode: str, filters: dict[str, Any], result_count: int
    ) -> None:
        try:
            self._execute(
                "INSERT INTO search_history (query, mode, filters, result_count, created_at)"
                " VALUES (?,?,?,?,?)",
                (query[:1000], mode, _json(filters), result_count, _utc_now()),
                commit=True,
            )
        except sqlite3.Error as exc:  # pragma: no cover
            log.debug("cannot log search: %s", exc)

    def recent_searches(self, limit: int = 20) -> list[sqlite3.Row]:
        return self._execute(
            "SELECT * FROM search_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def set_meta(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO meta (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value), commit=True,
        )

    def get_meta(self, key: str, default: str = "") -> str:
        row = self._execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def vacuum(self) -> None:
        try:
            self._execute("VACUUM", commit=True)
        except sqlite3.Error as exc:  # pragma: no cover
            log.warning("VACUUM failed: %s", exc)
