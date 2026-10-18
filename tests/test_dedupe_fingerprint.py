"""Title fingerprint blocking + schema migration tests."""

from __future__ import annotations

import sqlite3
import time

import pytest

from database.db import SCHEMA_VERSION, Database
from models.paper import Paper
from utils.title_normalizer import title_fingerprint, title_similarity

pytestmark = pytest.mark.skipif(
    sqlite3.sqlite_version_info < (3, 35, 0),
    reason="ALTER TABLE DROP COLUMN requires SQLite 3.35+",
)

V1_SCHEMA = """
CREATE TABLE papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '[]',
    year INTEGER,
    venue TEXT,
    doi TEXT,
    arxiv_id TEXT,
    abstract TEXT,
    keywords TEXT NOT NULL DEFAULT '[]',
    oa_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    pdf_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
    local_path TEXT,
    source_url TEXT,
    pdf_urls TEXT NOT NULL DEFAULT '[]',
    sources TEXT NOT NULL DEFAULT '[]',
    citation_count INTEGER,
    relevance_score REAL,
    notes TEXT,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    downloaded_at TEXT,
    checked_at TEXT
);
CREATE UNIQUE INDEX ux_papers_doi ON papers(doi) WHERE doi IS NOT NULL AND doi <> '';
CREATE UNIQUE INDEX ux_papers_arxiv ON papers(arxiv_id) WHERE arxiv_id IS NOT NULL AND arxiv_id <> '';
CREATE INDEX ix_papers_norm_title ON papers(normalized_title);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _make_v1_database(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.execute(
        "INSERT INTO papers (title, normalized_title, discovered_at, updated_at)"
        " VALUES (?,?,?,?)",
        (
            "Optical SAR Change Detection for Building Damage Assessment",
            "optical sar change detection for building damage assessment",
            "2025-01-01T00:00:00+00:00",
            "2025-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()


def test_fingerprint_uses_leading_content_tokens():
    assert title_fingerprint("The Role of Fog in Change Detection") == "role fog change"
    assert title_fingerprint("Convolutional Global Context for Change Detection") == (
        "convolutional global context"
    )
    assert title_fingerprint("") == ""


def test_fingerprints_match_for_variants():
    a = "Convolutional Global Context for Change Detection"
    b = "Convolutional Global Context for Change Detecton"  # typo
    assert title_fingerprint(a) == title_fingerprint(b)
    assert title_similarity(a, b) >= 95


def test_cross_year_fuzzy_duplicates_are_merged(tmp_path):
    db = Database(tmp_path / "cross.db")
    try:
        first = Paper(
            title="Optical SAR Change Detection for Building Damage Assessment",
            year=2019, abstract="preprint",
        )
        first_id, created, _ = db.upsert(first)
        assert created

        # Same work one typo later, published two years later, no identifiers.
        later = Paper(
            title="Optical SAR Change Detection for Building Damge Assessment",
            year=2021, venue="IEEE TGRS",
        )
        second_id, created, reason = db.upsert(later)

        assert created is False
        assert second_id == first_id
        assert reason.startswith("fuzzy:")
        assert db.count() == 1
        assert db.get(first_id).year == 2019  # first year kept
    finally:
        db.close_all()


def test_large_library_dedupe_uses_bucket(tmp_path):
    db = Database(tmp_path / "large.db")
    try:
        papers = [
            Paper(title=f"Remote Sensing Study Number {i} on Multimodal Fusion", year=2020)
            for i in range(900)
        ]
        for paper in papers:
            db.insert(paper)
        assert db.count() == 900

        target = papers[450]
        started = time.monotonic()
        duplicate = Paper(
            title="Remote Sensing Study Number 450 on Multimodal Fusions",  # typo
            year=2024,
        )
        found_id, reason = db.find_existing(duplicate)
        elapsed = time.monotonic() - started

        assert found_id == target.id
        assert reason.startswith("fuzzy:")
        assert elapsed < 0.5, f"bucket lookup took {elapsed:.2f}s"
    finally:
        db.close_all()


def test_unrelated_titles_in_same_bucket_are_not_merged(tmp_path):
    db = Database(tmp_path / "bucket.db")
    try:
        a = Paper(title="Optical SAR Registration with Deep Features", year=2024)
        a_id, _, _ = db.upsert(a)
        b = Paper(title="Optical SAR Registration for Flood Mapping", year=2024)
        b_id, created, _ = db.upsert(b)
        assert created is True
        assert a_id != b_id
        assert db.count() == 2
    finally:
        db.close_all()


def test_fresh_database_records_schema_version(tmp_path):
    db = Database(tmp_path / "fresh.db")
    try:
        assert db.schema_version == SCHEMA_VERSION
        columns = {
            row["name"]
            for row in db.connection.execute("PRAGMA table_info(papers)").fetchall()
        }
        assert "title_key" in columns
    finally:
        db.close_all()


def test_v1_database_is_migrated_and_backed_up(tmp_path):
    path = tmp_path / "legacy.db"
    _make_v1_database(path)

    db = Database(path)
    try:
        assert db.schema_version == SCHEMA_VERSION
        columns = {
            row["name"]
            for row in db.connection.execute("PRAGMA table_info(papers)").fetchall()
        }
        assert "title_key" in columns
        row = db.connection.execute("SELECT title_key FROM papers").fetchone()
        assert row["title_key"] == "optical sar change"
        assert list(tmp_path.glob("legacy.db.pre-migration.*")), "no backup created"
    finally:
        db.close_all()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "idem.db"
    db = Database(path)
    db.insert(Paper(title="Idempotent Paper on Cloud Removal", year=2025))
    db.close_all()

    for _ in range(3):
        reopened = Database(path)
        assert reopened.schema_version == SCHEMA_VERSION
        assert reopened.count() == 1
        reopened.close_all()
