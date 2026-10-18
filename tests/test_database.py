"""SQLite persistence tests."""

from __future__ import annotations

import pytest

from database.db import Database
from models.paper import OaStatus, PdfCandidate, PdfStatus, Paper
from utils.title_normalizer import normalize_title


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


def test_insert_and_read_roundtrip(db, sample_paper):
    paper_id = db.insert(sample_paper)
    assert paper_id > 0
    assert db.count() == 1

    stored = db.get(paper_id)
    assert stored is not None
    assert stored.title == sample_paper.title
    assert stored.authors == sample_paper.authors
    assert stored.doi == sample_paper.doi
    assert stored.arxiv_id == sample_paper.arxiv_id
    assert stored.keywords == sample_paper.keywords
    assert stored.sources == sample_paper.sources
    assert stored.pdf_urls == [c.url for c in sample_paper.pdf_candidates]
    assert stored.oa_status == OaStatus.OPEN_ACCESS


def test_normalized_title_column_is_populated(db, sample_paper):
    paper_id = db.insert(sample_paper)
    row = db.connection.execute(
        "SELECT normalized_title FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    assert row["normalized_title"] == normalize_title(sample_paper.title)


def test_upsert_same_doi_updates_instead_of_inserting(db):
    first = Paper(title="Optical SAR Registration", doi="10.1109/TGRS.2024.0001")
    second = Paper(
        title="Optical SAR Registration",
        doi="10.1109/TGRS.2024.0001",
        abstract="Now with an abstract",
        year=2024,
        venue="IEEE TGRS",
    )
    id1, created1, reason1 = db.upsert(first)
    id2, created2, reason2 = db.upsert(second)
    assert created1 is True
    assert created2 is False
    assert id1 == id2
    assert reason2 == "doi"
    assert db.count() == 1
    stored = db.get(id1)
    assert stored.abstract == "Now with an abstract"
    assert stored.venue == "IEEE TGRS"


def test_upsert_same_title_different_doi_merges(db):
    a = Paper(title="Cloud Removal with Diffusion Models")
    b = Paper(title="Cloud removal with diffusion models!", doi="10.1016/j.rse.2024.0002")
    id1, created1, _ = db.upsert(a)
    id2, created2, reason = db.upsert(b)
    assert created1 and not created2
    assert id1 == id2
    assert reason == "title"
    assert db.get(id1).doi == "10.1016/j.rse.2024.0002"


def test_fuzzy_title_dedupe(db):
    a = Paper(title="Convolutional Global Context for Change Detection", year=2025)
    b = Paper(title="Convolutional Global Context for Change Detecton", year=2025)
    id1, created1, _ = db.upsert(a)
    id2, created2, reason = db.upsert(b)
    assert created1 and not created2
    assert id1 == id2
    assert reason.startswith("fuzzy:")


def test_distinct_papers_are_inserted(db):
    db.upsert(Paper(title="Paper One", doi="10.1000/one"))
    db.upsert(Paper(title="Completely Different Work", doi="10.1000/two"))
    assert db.count() == 2


def test_update_download_state(db, sample_paper):
    paper_id = db.insert(sample_paper)
    local = "C:/library/2026/CVPR/x.pdf"
    db.update_download_state(
        paper_id, PdfStatus.VALIDATED, local_path=local, oa_status=OaStatus.OPEN_ACCESS
    )
    stored = db.get(paper_id)
    assert stored.pdf_status == PdfStatus.VALIDATED
    assert stored.local_pdf_path == local
    assert stored.downloaded_at


def test_stats_counts_by_status(db):
    a = Paper(title="Paper A", doi="10.1000/a")
    b = Paper(title="Paper B", doi="10.1000/b")
    c = Paper(title="Paper C", doi="10.1000/c")
    ida, _, _ = db.upsert(a)
    idb, _, _ = db.upsert(b)
    idc, _, _ = db.upsert(c)
    db.update_download_state(ida, PdfStatus.VALIDATED, "a.pdf")
    db.update_download_state(idb, PdfStatus.PAYWALLED_ONLY)
    db.update_download_state(idc, PdfStatus.NO_PUBLIC_COPY_FOUND)

    stats = db.stats()
    assert stats.total == 3
    assert stats.with_pdf == 1
    assert stats.validated == 1
    assert stats.paywalled == 1
    assert stats.no_public_copy == 1


def test_list_papers_only_local_and_search(db):
    a = Paper(title="Optical SAR Fusion", doi="10.1000/l1")
    b = Paper(title="Unrelated Work", doi="10.1000/l2")
    ida, _, _ = db.upsert(a)
    db.upsert(b)
    db.update_download_state(ida, PdfStatus.VALIDATED, "a.pdf")

    local = db.list_papers(only_local=True)
    assert [p.title for p in local] == ["Optical SAR Fusion"]
    assert db.list_papers(search="Unrelated")


def test_download_log_and_history(db, sample_paper):
    paper_id = db.insert(sample_paper)
    db.log_download(sample_paper, "VALIDATED", "https://x/y.pdf", "arXiv", "ok")
    db.add_search_history("optical SAR", "keywords", {"year_from": 2020}, 12)
    assert len(db.recent_downloads()) == 1
    assert len(db.recent_searches()) == 1
    assert db.count() == 1


def test_meta_roundtrip(db):
    db.set_meta("last_run", "2026-01-01")
    assert db.get_meta("last_run") == "2026-01-01"
    assert db.get_meta("missing", "fallback") == "fallback"


def test_upsert_records_sources(db, sample_paper):
    paper_id, _, _ = db.upsert(sample_paper)
    rows = db.connection.execute(
        "SELECT provider FROM paper_sources WHERE paper_id = ? ORDER BY provider",
        (paper_id,),
    ).fetchall()
    assert [row["provider"] for row in rows] == ["arxiv", "openalex"]


def test_find_existing_reports_reason(db):
    paper = Paper(title="Building Damage Assessment from Satellite Imagery",
                  doi="10.1016/j.rse.2025.777", year=2025)
    pid, _, _ = db.upsert(paper)
    found, reason = db.find_existing(Paper(title="Other", doi="10.1016/j.rse.2025.777"))
    assert found == pid and reason == "doi"

    found, reason = db.find_existing(Paper(title="Building Damage Assessment from Satellite Imagery"))
    assert found == pid and reason == "title"
