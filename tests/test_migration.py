"""V1.0 -> V1.1 data migration tests (sandboxed, no real user data)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import utils.config as config_module
from database.db import Database
from models.paper import Paper
from services.migration_service import MARKER_NAME, MigrationService
from utils.config import load_settings


def _make_legacy_home(root: Path, papers: int = 3, library_files: int = 2) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    db = Database(root / "database" / "paperradar.db")
    for index in range(papers):
        db.insert(
            Paper(
                title=f"Legacy Remote Sensing Paper {index}",
                doi=f"10.1000/legacy.{index}",
                year=2024,
                venue="IEEE TGRS",
            )
        )
    db.close_all()

    library = root / "library" / "2024" / "TGRS"
    library.mkdir(parents=True, exist_ok=True)
    for index in range(library_files):
        (library / f"legacy_{index}.pdf").write_bytes(
            b"%PDF-1.4\n% legacy test file\n%%EOF\n"
        )

    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "research_profile.yaml").write_text(
        "name: Migrated Profile\nkeywords:\n  - cloud removal\n", encoding="utf-8"
    )
    (config_dir / "config.yaml").write_text(
        "api:\n  openalex_mailto: migrated@example.com\n", encoding="utf-8"
    )
    return root


@pytest.fixture()
def sandbox(tmp_path):
    legacy = _make_legacy_home(tmp_path / "legacy", papers=3, library_files=2)
    target = tmp_path / "appdata" / "PaperRadar" / "data"
    return legacy, target


def test_migration_copies_database_library_and_config(sandbox):
    legacy, target = sandbox
    report = MigrationService(legacy, target).run()

    assert report.success, report.error
    assert report.papers == 3
    assert report.library_files == 2
    assert report.backup is not None and report.backup.is_dir()
    assert (report.backup / "paperradar.db").is_file()

    db = Database(target / "database" / "paperradar.db")
    try:
        assert db.count() == 3
    finally:
        db.close_all()

    assert sorted(p.name for p in (target / "library").rglob("*.pdf")) == [
        "legacy_0.pdf", "legacy_1.pdf"
    ]
    assert (target / "config" / "research_profile.yaml").is_file()
    assert (target / "config" / "user_config.yaml").is_file()

    marker = json.loads((target / MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["papers"] == 3
    assert marker["version"] == 1

    # Legacy data must never be destroyed by the migration.
    assert (legacy / "database" / "paperradar.db").is_file()
    assert (legacy / "library" / "2024" / "TGRS" / "legacy_0.pdf").is_file()


def test_migration_is_idempotent(sandbox):
    legacy, target = sandbox
    service = MigrationService(legacy, target)
    first = service.run()
    assert first.success

    marker = target / MARKER_NAME
    first_marker = marker.read_text(encoding="utf-8")

    second = service.run()
    assert second.success
    assert second.skipped
    assert second.papers == 0
    assert marker.read_text(encoding="utf-8") == first_marker

    db = Database(target / "database" / "paperradar.db")
    try:
        assert db.count() == 3
    finally:
        db.close_all()


def test_migration_failure_keeps_legacy_data(sandbox):
    legacy, target = sandbox
    # Corrupt the legacy database: quick_check must fail after copying.
    db_path = legacy / "database" / "paperradar.db"
    db_path.write_bytes(b"this is not a sqlite database")

    report = MigrationService(legacy, target).run()

    assert report.success is False
    assert report.error
    assert db_path.exists()
    assert not (target / "database" / "paperradar.db").exists()
    assert not (target / MARKER_NAME).exists()


def test_resolve_home_migrates_and_caches(sandbox, monkeypatch):
    legacy, target = sandbox
    monkeypatch.delenv("PAPERRADAR_HOME", raising=False)
    monkeypatch.setattr(config_module, "default_data_root", lambda: target)
    monkeypatch.setattr(config_module, "legacy_home_candidates", lambda: [legacy])

    resolved = config_module.resolve_home()
    assert resolved == target
    assert (target / MARKER_NAME).exists()
    report = config_module.LAST_MIGRATION
    assert report is not None and report.success

    # Second start: marker short-circuits, no second migration.
    marker_before = (target / MARKER_NAME).read_text(encoding="utf-8")
    resolved_again = config_module.resolve_home()
    assert resolved_again == target
    assert (target / MARKER_NAME).read_text(encoding="utf-8") == marker_before


def test_load_settings_reads_migrated_config(sandbox):
    legacy, target = sandbox
    MigrationService(legacy, target).run()

    settings = load_settings(home=target, migrate=False)
    assert settings.api["openalex_mailto"] == "migrated@example.com"
    assert settings.profile["name"] == "Migrated Profile"
    assert settings.paths.home == target
    assert settings.paths.db_path == target / "database" / "paperradar.db"


def test_new_install_uses_localappdata_layout(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERRADAR_HOME", raising=False)
    monkeypatch.setattr(
        config_module, "default_data_root", lambda: tmp_path / "localappdata"
    )
    monkeypatch.setattr(config_module, "legacy_home_candidates", lambda: [])

    settings = load_settings(migrate=True)
    root = settings.paths.home
    assert root == tmp_path / "localappdata"
    assert settings.paths.database_dir == root / "database"
    assert settings.paths.library_dir == root / "library"
    assert settings.paths.config_path.name == "user_config.yaml"
    assert settings.paths.profile_path.exists()
