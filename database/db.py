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

from models.paper import OaStatus, PdfCandidate, PdfStatus, Paper, clean_arxiv_id, clean_doi
from utils.title_normalizer import (
    normalize_title,
    title_fingerprint,
    title_similarity,
)

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 3


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
        self.corruption_note = ""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._quarantine_if_corrupt()
        self._initialise()

    def _quarantine_if_corrupt(self) -> None:
        """Detect a damaged database and move it aside before opening it.

        The corrupt file is *never deleted*: it is renamed to
        ``paperradar.db.corrupt.<timestamp>`` and the application starts with a
        fresh, empty database so the user can keep working.
        """
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size == 0:
                return
        except OSError:  # pragma: no cover
            return

        result = "ok"
        try:
            probe = sqlite3.connect(str(self.path), timeout=5.0)
            try:
                row = probe.execute("PRAGMA quick_check").fetchone()
                result = str(row[0]) if row else "unknown"
            finally:
                probe.close()
        except sqlite3.DatabaseError as exc:
            result = f"{type(exc).__name__}: {exc}"

        if result.strip().lower() == "ok":
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.path.with_name(f"{self.path.name}.corrupt.{stamp}")
        try:
            self.path.replace(target)
        except OSError as exc:
            raise RuntimeError(
                f"数据库已损坏且无法备份（{result}）：{exc}"
            ) from exc
        self.corruption_note = (
            f"数据库损坏（{result}），已备份为 {target.name}，本次以空数据库启动"
        )
        log.critical(self.corruption_note)

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
        self._migrate_schema()
        log.info("database ready: %s (schema v%s)", self.path, self.schema_version)

    # ------------------------------------------------------------------ #
    # Schema versioning
    # ------------------------------------------------------------------ #
    @property
    def schema_version(self) -> int:
        try:
            return int(self.get_meta("schema_version", "0") or 0)
        except (TypeError, ValueError):
            return 0

    def _migrate_schema(self) -> None:
        """Apply idempotent schema upgrades, backing up before changing data."""
        paper_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(papers)").fetchall()
        }
        log_columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(download_logs)"
            ).fetchall()
        }
        add_title_key = "title_key" not in paper_columns
        add_http_status = "http_status" not in log_columns

        if add_title_key or add_http_status:
            backup = self.backup(suffix="pre-migration") if self.count() > 0 else None
            if backup is not None:
                log.info("database backed up before schema upgrade: %s", backup)

            if add_title_key:
                self.connection.execute("ALTER TABLE papers ADD COLUMN title_key TEXT")
                self.connection.commit()
                self._backfill_title_keys()
                self.connection.execute(
                    "CREATE INDEX IF NOT EXISTS ix_papers_title_key ON papers(title_key)"
                )
                log.info("schema: added papers.title_key")
            if add_http_status:
                self.connection.execute(
                    "ALTER TABLE download_logs ADD COLUMN http_status INTEGER"
                )
                log.info("schema: added download_logs.http_status")
            self.connection.commit()
            self.set_meta("schema_migrated_at", _utc_now())

        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_papers_title_key ON papers(title_key)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_papers_norm_title ON papers(normalized_title)"
        )
        self.connection.commit()

        if self.schema_version != SCHEMA_VERSION:
            self.set_meta("schema_version", str(SCHEMA_VERSION))

    def _backfill_title_keys(self) -> None:
        rows = self.connection.execute(
            "SELECT id, title FROM papers WHERE title_key IS NULL"
        ).fetchall()
        if not rows:
            return
        payload = [
            (title_fingerprint(row["title"]), int(row["id"])) for row in rows
        ]
        with self._write_lock:
            self.connection.executemany(
                "UPDATE papers SET title_key = ? WHERE id = ?", payload
            )
            self.connection.commit()
        log.info("backfilled title fingerprints for %d papers", len(payload))

    def backup(self, suffix: str = "bak", keep: int = 3) -> Optional[Path]:
        """Copy the database next to itself and prune old backups."""
        import shutil

        if not self.path.exists():
            return None
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.path.with_name(f"{self.path.name}.{suffix}.{stamp}")
        try:
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy2(self.path, target)
        except (OSError, sqlite3.Error) as exc:  # pragma: no cover - disk issues
            log.warning("database backup failed: %s", exc)
            return None
        self._prune_backups(suffix=suffix, keep=keep)
        return target

    def _prune_backups(self, suffix: str = "bak", keep: int = 3) -> None:
        try:
            backups = sorted(self.path.parent.glob(f"{self.path.name}.{suffix}.*"))
        except OSError:  # pragma: no cover
            return
        for old in backups[:-keep] if keep > 0 else []:
            try:
                old.unlink()
            except OSError:  # pragma: no cover
                pass

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
        # Normalise identifiers on every write: DOIs are case-insensitive and a
        # mixed-case value would slip past the unique index (SQLite compares
        # TEXT case-sensitively) and create duplicates.
        doi = clean_doi(paper.doi)
        arxiv_id = clean_arxiv_id(paper.arxiv_id)
        paper.doi = doi
        paper.arxiv_id = arxiv_id
        return (
            paper.title,
            normalize_title(paper.title),
            title_fingerprint(paper.title),
            _json(paper.authors),
            paper.year,
            paper.venue,
            doi,
            arxiv_id,
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

        # Fuzzy pass over the fingerprint bucket only.  The bucket is keyed on
        # the leading content words, so the cost depends on how many papers
        # share that prefix — never on the size of the library — and it does not
        # care about the publication year (arXiv vs journal versions often
        # differ by one year).
        key = title_fingerprint(paper.title)
        if not key:
            return None, ""
        rows = self._execute(
            "SELECT id, title FROM papers WHERE title_key = ? LIMIT 500", (key,)
        ).fetchall()

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
        """Insert or merge ``paper``; returns ``(id, created, reason)``.

        Never raises ``IntegrityError`` for a duplicate DOI/arXiv id: races and
        records that collide after the lookup are merged into the row that owns
        the identifier.
        """
        existing_id, reason = self.find_existing(paper, fuzzy_threshold)
        if existing_id is not None:
            existing = self.get(existing_id)
            if existing is not None:
                existing.merge_from(paper)
                existing.id = existing_id
                self.update(existing)
                self._record_sources(existing.id, paper)
                return existing.id, False, reason
            self._record_sources(existing_id, paper)
            return existing_id, False, reason

        try:
            paper_id = self.insert(paper)
        except sqlite3.IntegrityError:
            owner_id = self._find_conflict_owner(paper)
            if owner_id is None:  # pragma: no cover - unknown constraint
                raise
            self._merge_duplicate_rows(owner_id, None, paper)
            self._record_sources(owner_id, paper)
            log.info("merged duplicate %r into row #%s after IntegrityError",
                     paper.title[:60], owner_id)
            return owner_id, False, "conflict"

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
                title, normalized_title, title_key, authors, year, venue, doi,
                arxiv_id, abstract, keywords, oa_status, pdf_status, local_path,
                source_url, pdf_urls, sources, citation_count, relevance_score,
                discovered_at, updated_at, downloaded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        cursor = self._execute(sql, values, commit=True)
        paper.id = int(cursor.lastrowid or 0)
        log.debug("inserted paper #%s: %s", paper.id, paper.title[:60])
        return paper.id

    def update(self, paper: Paper) -> None:
        """Update a row, merging into the identifier owner on a unique clash."""
        if paper.id is None:
            raise ValueError("paper.id is required for update()")
        try:
            self._update_raw(paper)
        except sqlite3.IntegrityError:
            owner_id = self._find_conflict_owner(paper, exclude_id=paper.id)
            owner = self.get(owner_id) if owner_id is not None else None
            if owner is None or owner.id is None:
                raise
            target_id, source_id = self._canonical_pair(paper, owner)
            self._merge_duplicate_rows(target_id, source_id, paper)
            paper.id = target_id
            log.info("merged duplicate row #%s into #%s after IntegrityError",
                     source_id, target_id)

    def _update_raw(self, paper: Paper, conn: Optional[sqlite3.Connection] = None) -> None:
        params = self._paper_params(paper) + (
            _utc_now(), paper.downloaded_at, paper.id,
        )
        sql = """
            UPDATE papers SET
                title = ?, normalized_title = ?, title_key = ?, authors = ?,
                year = ?, venue = ?, doi = ?, arxiv_id = ?, abstract = ?,
                keywords = ?, oa_status = ?, pdf_status = ?, local_path = ?,
                source_url = ?, pdf_urls = ?, sources = ?, citation_count = ?,
                relevance_score = ?, updated_at = ?, downloaded_at = ?
            WHERE id = ?
        """
        if conn is not None:
            conn.execute(sql, params)
            return
        self._execute(sql, params, commit=True)

    # ------------------------------------------------------------------ #
    # Duplicate resolution
    # ------------------------------------------------------------------ #
    def _find_conflict_owner(
        self, paper: Paper, exclude_id: Optional[int] = None
    ) -> Optional[int]:
        """Return the id of a *different* row that owns one of our identifiers."""
        checks: list[tuple[str, Optional[str]]] = [
            ("doi", paper.doi),
            ("arxiv_id", paper.arxiv_id),
        ]
        for column, value in checks:
            if not value:
                continue
            row = self._execute(
                f"SELECT id FROM papers WHERE {column} = ? AND id <> ?",
                (value, exclude_id if exclude_id is not None else -1),
            ).fetchone()
            if row:
                return int(row["id"])
        return None

    @staticmethod
    def _row_priority(paper: Paper) -> int:
        """Higher wins: a validated local PDF is the most valuable record."""
        if paper.local_pdf_path and paper.pdf_status == PdfStatus.VALIDATED:
            return 4
        if paper.local_pdf_path:
            return 3
        if paper.doi:
            return 2
        if paper.arxiv_id:
            return 1
        return 0

    def _canonical_pair(self, incoming: Paper, owner: Paper) -> tuple[int, int]:
        """Decide which row survives: returns ``(target_id, source_id)``."""
        assert incoming.id is not None and owner.id is not None
        incoming_priority = self._row_priority(incoming)
        owner_priority = self._row_priority(owner)
        if incoming_priority > owner_priority:
            return incoming.id, owner.id
        if owner_priority > incoming_priority:
            return owner.id, incoming.id
        # Same rank: keep the older row so provenance stays stable.
        if owner.id <= incoming.id:
            return owner.id, incoming.id
        return incoming.id, owner.id

    def _merge_duplicate_rows(
        self,
        target_id: int,
        source_id: Optional[int],
        incoming: Optional[Paper] = None,
    ) -> int:
        """Merge ``source_id`` (and optional ``incoming``) into ``target_id``.

        Runs inside a single transaction: delete the source row first so the
        unique DOI/arXiv indexes are free, then write the merged row and move
        provenance (``paper_sources`` / ``paper_tags`` / ``download_logs``).
        """
        with self._write_lock:
            conn = self.connection
            try:
                target = self.get(target_id)
                if target is None:  # pragma: no cover - defensive
                    conn.rollback()
                    return target_id
                merged = target
                if source_id is not None and source_id != target_id:
                    source = self.get(source_id)
                    if source is not None:
                        merged.merge_from(source)
                if incoming is not None:
                    merged.merge_from(incoming)
                merged.id = target_id

                if source_id is not None and source_id != target_id:
                    # Move provenance BEFORE deleting the row: paper_sources and
                    # paper_tags are ON DELETE CASCADE and would vanish.
                    conn.execute(
                        "UPDATE OR IGNORE paper_sources SET paper_id = ? WHERE paper_id = ?",
                        (target_id, source_id),
                    )
                    conn.execute("DELETE FROM paper_sources WHERE paper_id = ?", (source_id,))
                    conn.execute(
                        "UPDATE OR IGNORE paper_tags SET paper_id = ? WHERE paper_id = ?",
                        (target_id, source_id),
                    )
                    conn.execute("DELETE FROM paper_tags WHERE paper_id = ?", (source_id,))
                    conn.execute(
                        "UPDATE download_logs SET paper_id = ? WHERE paper_id = ?",
                        (target_id, source_id),
                    )
                    conn.execute("DELETE FROM papers WHERE id = ?", (source_id,))
                self._update_raw(merged, conn=conn)
                conn.commit()
                return target_id
            except Exception:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                raise

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
        http_status: Optional[int] = None,
    ) -> None:
        try:
            self._execute(
                "INSERT INTO download_logs"
                " (paper_id, title, url, provider, oa_status, status, reason, http_status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (paper.id, paper.title[:500], url[:2000], provider,
                 paper.oa_status.value, status, reason[:1000], http_status, _utc_now()),
                commit=True,
            )
        except sqlite3.Error as exc:  # pragma: no cover
            log.debug("cannot log download: %s", exc)

    def download_logs(self, limit: int = 5000) -> list[sqlite3.Row]:
        """Download history joined with the paper's DOI and local path."""
        return self._execute(
            "SELECT d.id, d.paper_id, d.title, d.url, d.provider, d.status,"
            " d.reason, d.http_status, d.created_at,"
            " p.doi AS doi, p.local_path AS local_path"
            " FROM download_logs d LEFT JOIN papers p ON p.id = d.paper_id"
            " ORDER BY d.id DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()

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
