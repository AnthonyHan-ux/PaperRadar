"""One-time migration of V1.0 runtime data into the V1.1 data directory.

V1.0 kept its SQLite database, PDF library and config next to the program
(``<program>/database/paperradar.db`` …).  V1.1 moves everything under
``%LOCALAPPDATA%\\PaperRadar\\data`` so the program directory can be read-only.

Rules implemented here:

* a timestamped backup is written before anything is copied;
* the database is copied with SQLite's online backup API (WAL-safe), then
  ``quick_check``-ed and row-counted against the source;
* the PDF library and configuration are copied (never moved, never deleted);
* a ``migration.json`` marker makes the operation idempotent;
* any failure leaves the legacy directory untouched and the caller falls back
  to using it, so the user never boots into an empty library.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

MARKER_NAME = "migration.json"
MIGRATION_VERSION = 1


@dataclass
class MigrationReport:
    attempted: bool = False
    success: bool = False
    source: Optional[Path] = None
    target: Optional[Path] = None
    backup: Optional[Path] = None
    papers: int = 0
    library_files: int = 0
    notes: list[str] = field(default_factory=list)
    error: str = ""
    skipped: str = ""

    def summary(self) -> str:
        if self.skipped:
            return f"数据迁移跳过：{self.skipped}"
        if not self.success:
            return f"数据迁移失败：{self.error}"
        return (
            f"已迁移 {self.papers} 篇论文、{self.library_files} 个 PDF"
            f"（{self.source} → {self.target}）"
        )


class MigrationService:
    """Copies a V1.0 data directory into the V1.1 location."""

    def __init__(self, source: Path, target: Path) -> None:
        self.source = Path(source)
        self.target = Path(target)

    # ------------------------------------------------------------------ #
    @staticmethod
    def marker_path(target: Path) -> Path:
        return Path(target) / MARKER_NAME

    def already_migrated(self) -> bool:
        return self.marker_path(self.target).exists()

    # ------------------------------------------------------------------ #
    def run(self) -> MigrationReport:
        report = MigrationReport(
            attempted=True, source=self.source, target=self.target
        )
        if self.already_migrated():
            report.success = True
            report.skipped = "已完成过迁移（migration.json 存在）"
            return report
        if not self.source.exists():
            report.success = True
            report.skipped = "没有旧数据目录"
            return report

        legacy_db = self.source / "database" / "paperradar.db"
        target_db = self.target / "database" / "paperradar.db"
        created_target_db = False

        try:
            self.target.mkdir(parents=True, exist_ok=True)
            if legacy_db.is_file() and not target_db.exists():
                report.backup = self._backup(legacy_db)
                # Mark before copying: a failed copy can still leave a partial
                # file behind, which must not be mistaken for a good database.
                created_target_db = True
                self._copy_database(legacy_db, target_db)
                report.papers = self._verify_database(legacy_db, target_db)
            elif target_db.is_file():
                report.papers = self._count_papers(target_db)
                report.notes.append("目标数据库已存在，保留现有数据库")

            report.library_files = self._migrate_library()
            self._migrate_config()
            self._migrate_exports()
            self._write_marker(report)
            report.success = True
            log.info("migration complete: %s", report.summary())
            return report
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            report.error = f"{type(exc).__name__}: {exc}"
            report.success = False
            log.error("migration failed: %s", report.error)
            if created_target_db:
                try:
                    target_db.unlink(missing_ok=True)
                except OSError:  # pragma: no cover
                    pass
            return report

    # ------------------------------------------------------------------ #
    # Database
    # ------------------------------------------------------------------ #
    def _backup(self, legacy_db: Path) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.target / f"migration_backup_{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_db, backup_dir / legacy_db.name)
        legacy_config = self.source / "config"
        if legacy_config.is_dir():
            shutil.copytree(
                legacy_config, backup_dir / "config", dirs_exist_ok=True
            )
        return backup_dir

    @staticmethod
    def _copy_database(source: Path, target: Path) -> None:
        """WAL-safe copy via SQLite's online backup API."""
        target.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(str(source))
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    @staticmethod
    def _count_papers(path: Path) -> int:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("SELECT COUNT(*) FROM papers").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    @staticmethod
    def _check_integrity(path: Path) -> None:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            result = str(row[0]) if row else "unknown"
            if result.lower() != "ok":
                raise RuntimeError(f"数据库完整性检查失败：{result}")
        finally:
            conn.close()

    def _verify_database(self, legacy_db: Path, target_db: Path) -> int:
        self._check_integrity(target_db)
        before = self._count_papers(legacy_db)
        after = self._count_papers(target_db)
        if before != after:
            raise RuntimeError(f"迁移后论文数不一致：{before} → {after}")
        return after

    # ------------------------------------------------------------------ #
    # Files
    # ------------------------------------------------------------------ #
    def _migrate_library(self) -> int:
        legacy_lib = self.source / "library"
        target_lib = self.target / "library"
        if not legacy_lib.is_dir():
            return 0
        if target_lib.is_dir() and any(target_lib.rglob("*.pdf")):
            log.info("target library already populated; keeping it")
            return self._count_pdfs(target_lib)
        shutil.copytree(legacy_lib, target_lib, dirs_exist_ok=True)
        copied = self._count_pdfs(target_lib)
        expected = self._count_pdfs(legacy_lib)
        if copied < expected:
            raise RuntimeError(f"文献库文件数不一致：{expected} → {copied}")
        return copied

    def _migrate_config(self) -> None:
        legacy_config = self.source / "config"
        if not legacy_config.is_dir():
            return
        target_config = self.target / "config"
        target_config.mkdir(parents=True, exist_ok=True)

        profile = legacy_config / "research_profile.yaml"
        if profile.is_file() and not (target_config / profile.name).exists():
            shutil.copy2(profile, target_config / profile.name)

        # V1.0 used config/config.yaml for user settings; V1.1 reads both, but
        # copy it to user_config.yaml so the new file is complete.
        legacy_user = legacy_config / "config.yaml"
        new_user = target_config / "user_config.yaml"
        if legacy_user.is_file() and not new_user.exists():
            shutil.copy2(legacy_user, new_user)

    def _migrate_exports(self) -> None:
        legacy_exports = self.source / "exports"
        if legacy_exports.is_dir():
            shutil.copytree(
                legacy_exports, self.target / "exports", dirs_exist_ok=True
            )

    @staticmethod
    def _count_pdfs(root: Path) -> int:
        try:
            return sum(1 for _ in root.rglob("*.pdf"))
        except OSError:  # pragma: no cover
            return 0

    def _write_marker(self, report: MigrationReport) -> None:
        payload = {
            "version": MIGRATION_VERSION,
            "migrated_at": datetime.now().isoformat(timespec="seconds"),
            "source": str(self.source),
            "papers": report.papers,
            "library_files": report.library_files,
            "notes": report.notes,
        }
        marker = self.marker_path(self.target)
        marker.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
