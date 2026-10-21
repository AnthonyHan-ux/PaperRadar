"""Recovery paths: corrupt database, corrupt config, stale temp files."""

from __future__ import annotations

import os
import time
import sqlite3

import pytest

from database.db import Database
from models.paper import Paper
from utils.config import load_settings


@pytest.mark.parametrize('message', ['unable to open database file', 'database is locked', 'attempt to write a readonly database'])
def test_access_failures_do_not_quarantine_a_healthy_database(tmp_path, monkeypatch, message):
    path = tmp_path / 'paperradar.db'
    original = Database(path)
    original.insert(Paper(title='Keep Existing Research'))
    original.close_all()
    content = path.read_bytes()
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError(message)
    monkeypatch.setattr(sqlite3, 'connect', fail)
    with pytest.raises(sqlite3.OperationalError):
        Database(path)
    assert path.is_file()
    assert path.read_bytes() == content
    assert not list(tmp_path.glob('paperradar.db.corrupt.*'))


def test_corrupt_database_is_quarantined_not_deleted(tmp_path):
    path = tmp_path / "paperradar.db"
    path.write_bytes(b"definitely not a sqlite database at all")

    db = Database(path)
    try:
        assert db.corruption_note
        assert db.count() == 0
        backups = list(tmp_path.glob("paperradar.db.corrupt.*"))
        assert len(backups) == 1
        assert backups[0].read_bytes().startswith(b"definitely not")
        # The fresh database is usable.
        db.insert(Paper(title="Fresh Start Paper", doi="10.1000/fresh"))
        assert db.count() == 1
    finally:
        db.close_all()


def test_valid_database_is_not_quarantined(tmp_path):
    path = tmp_path / "paperradar.db"
    first = Database(path)
    first.insert(Paper(title="Existing Paper", doi="10.1000/existing"))
    first.close_all()

    second = Database(path)
    try:
        assert second.corruption_note == ""
        assert second.count() == 1
        assert not list(tmp_path.glob("paperradar.db.corrupt.*"))
    finally:
        second.close_all()


def test_broken_config_is_backed_up_and_defaults_used(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    broken = config_dir / "user_config.yaml"
    broken.write_text("api: [unclosed\n  - broken: : :\n", encoding="utf-8")

    settings = load_settings(home=tmp_path)

    assert settings is not None
    assert settings.api["openalex_mailto"] == ""
    backups = list(config_dir.glob("user_config.broken.*.yaml"))
    assert len(backups) == 1
    assert "unclosed" in backups[0].read_text(encoding="utf-8")


def test_stale_partial_downloads_are_cleaned(tmp_path):
    from services.app_service import AppService

    settings = load_settings(home=tmp_path)
    settings.config["search"]["providers"] = []
    service = AppService(settings=settings)
    try:
        tmp = settings.paths.tmp_dir
        tmp.mkdir(parents=True, exist_ok=True)
        stale = tmp / "old.part"
        fresh = tmp / "new.part"
        stale.write_bytes(b"x")
        fresh.write_bytes(b"x")
        old = time.time() - 48 * 3600
        os.utime(stale, (old, old))

        removed = service._cleanup_stale_temp_files(max_age_hours=24)

        assert removed == 1
        assert not stale.exists()
        assert fresh.exists()
    finally:
        service.close()


def test_clear_cache_only_touches_cache(tmp_path):
    from services.app_service import AppService

    settings = load_settings(home=tmp_path)
    settings.config["search"]["providers"] = []
    service = AppService(settings=settings)
    try:
        cache_file = settings.paths.cache_dir / "response.json"
        cache_file.write_text("{}", encoding="utf-8")
        library_file = settings.paths.library_dir / "keep.pdf"
        library_file.parent.mkdir(parents=True, exist_ok=True)
        library_file.write_bytes(b"%PDF-1.4")

        removed = service.clear_cache()

        assert removed == 1
        assert not cache_file.exists()
        assert library_file.exists()
        assert (settings.paths.database_dir / "paperradar.db").exists()
    finally:
        service.close()
