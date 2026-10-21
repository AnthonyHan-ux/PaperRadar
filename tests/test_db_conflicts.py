"""Duplicate-key conflict handling in the database.

A merge that introduces a DOI owned by another row must never surface an
``IntegrityError`` in the UI: the two rows are merged into one canonical record.
"""

from __future__ import annotations

import pytest

from database.db import Database
from models.paper import PdfStatus, Paper


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "conflict.db")
    yield database
    database.close_all()


def test_update_merges_doi_conflict_into_validated_row(tmp_path, db):
    # A: downloaded + validated, no DOI yet.
    a = Paper(title="Optical SAR Change Detection with Fusion", authors=["Wei Zhang"],
              year=2025, venue="IEEE TGRS")
    a_id, created, _ = db.upsert(a)
    assert created
    db.update_download_state(a_id, PdfStatus.VALIDATED, "C:/library/a.pdf")

    # B: same work, metadata-only, owns the DOI.
    b = Paper(title="Optical SAR Change Detection with Robust Fusion",
              doi="10.1109/TGRS.2025.999", year=2025, venue="IEEE TGRS",
              abstract="Published version abstract.")
    b_id, created, reason = db.upsert(b)
    assert created and b_id != a_id
    assert db.count() == 2

    # A later gains the DOI (e.g. Crossref lookup) -> unique index clash.
    a = db.get(a_id)
    a.doi = "10.1109/TGRS.2025.999"
    db.update(a)  # must not raise

    assert db.count() == 1
    merged = db.get(a_id)
    assert merged is not None
    # DOIs are normalised to lower case on write (case-insensitive identifier).
    assert merged.doi == "10.1109/tgrs.2025.999"
    assert merged.local_pdf_path == "C:/library/a.pdf"
    assert merged.pdf_status == PdfStatus.VALIDATED
    assert merged.abstract == "Published version abstract."
    assert db.get(b_id) is None


def test_conflict_merges_provenance(db):
    a = Paper(title="Cloud Removal with Diffusion", doi="10.1000/a", sources=["openalex"])
    a_id, _, _ = db.upsert(a)
    b = Paper(title="Cloud Removal with Diffusion Models", doi="10.1000/b",
              sources=["crossref", "arxiv"])
    b_id, _, _ = db.upsert(b)

    # Force the clash: B takes A's DOI.
    b = db.get(b_id)
    b.doi = "10.1000/a"
    db.update(b)

    assert db.count() == 1
    rows = db.connection.execute(
        "SELECT provider FROM paper_sources WHERE paper_id = ? ORDER BY provider",
        (a_id,),
    ).fetchall()
    providers = {row["provider"] for row in rows}
    assert {"openalex", "crossref", "arxiv"} <= providers


def test_upsert_race_duplicate_does_not_raise(db, monkeypatch):
    seed = Paper(title="Registered Paper", doi="10.1109/TGRS.2024.1", year=2024)
    seed_id, _, _ = db.upsert(seed)

    # Simulate a race: the lookup misses, the INSERT hits the unique index.
    monkeypatch.setattr(db, "find_existing", lambda *a, **k: (None, ""))
    racer = Paper(title="Totally Different Title", doi="10.1109/TGRS.2024.1",
                  abstract="Racer abstract", year=2024)
    merged_id, created, reason = db.upsert(racer)

    assert merged_id == seed_id
    assert created is False
    assert reason == "conflict"
    assert db.count() == 1
    assert db.get(seed_id).abstract == "Racer abstract"


def test_arxiv_conflict_is_resolved(db):
    a = Paper(title="First Title", arxiv_id="2501.00001")
    a_id, _, _ = db.upsert(a)
    b = Paper(title="Second Title", arxiv_id="2501.00001")
    b_id, _, _ = db.upsert(b)
    # Second upsert already matches by arXiv id -> merged, not inserted.
    assert a_id == b_id
    assert db.count() == 1


def test_distinct_dois_are_kept_even_when_titles_match(db):
    first = Paper(title="Tie Break Paper", doi="10.1000/tie1", year=2025)
    first_id, _, _ = db.upsert(first)
    second = Paper(title="Tie Break Paper", doi="10.1000/tie2", year=2025)
    second_id, _, _ = db.upsert(second)
    # A common title cannot erase a conflicting identifier.
    assert first_id != second_id
    assert db.count() == 2
    stored = db.get(first_id)
    assert stored.doi == "10.1000/tie1"
