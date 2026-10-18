"""Cross-provider merge / dedupe tests."""

from __future__ import annotations

from models.paper import Paper, PdfCandidate
from services.merge_service import MergeService, dedupe_candidates


def _paper(title: str, **kwargs) -> Paper:
    defaults = {"authors": ["A. Author"], "year": 2025}
    defaults.update(kwargs)
    return Paper(title=title, **defaults)


def test_same_doi_is_merged_and_fields_enriched():
    a = _paper("Attention Fusion for SAR-Optical Matching", doi="10.1109/TGRS.2025.123456")
    b = _paper(
        "Attention Fusion for SAR Optical Matching",
        doi="10.1109/TGRS.2025.123456",
        abstract="A much longer abstract that only Semantic Scholar has.",
        venue="IEEE TGRS",
        arxiv_id="2501.00001",
        pdf_candidates=[PdfCandidate("https://example.org/a.pdf", "Semantic Scholar")],
        sources=["semantic_scholar"],
    )
    merged, stats = MergeService().merge([[a], [b]])
    assert len(merged) == 1
    paper = merged[0]
    assert paper.abstract.startswith("A much longer abstract")
    assert paper.arxiv_id == "2501.00001"
    assert paper.venue == "IEEE TGRS"
    assert paper.pdf_urls == ["https://example.org/a.pdf"]
    assert set(paper.sources) == {"semantic_scholar"}
    assert stats.merged_by_doi == 1
    assert stats.duplicates_removed == 1


def test_same_arxiv_id_is_merged():
    a = _paper("Robust Cloud Removal for Optical Imagery", arxiv_id="2502.01234")
    b = _paper("Robust Cloud Removal for Optical Images", arxiv_id="2502.01234v3")
    merged, stats = MergeService().merge([[a], [b]])
    assert len(merged) == 1
    assert stats.merged_by_arxiv == 1


def test_exact_normalised_title_is_merged():
    a = _paper("Change Detection: A Survey of Deep Models")
    b = _paper("Change detection — a survey of deep models")
    merged, stats = MergeService().merge([[a], [b]])
    assert len(merged) == 1
    assert stats.merged_by_title_exact == 1


def test_fuzzy_title_is_merged():
    a = _paper("Convolutional Global Context for Change Detection")
    b = _paper("Convolutional Global Context for Change Detecton")  # typo
    merged, stats = MergeService().merge([[a], [b]])
    assert len(merged) == 1
    assert stats.merged_by_title_fuzzy == 1


def test_different_papers_are_not_merged():
    a = _paper("Optical SAR Change Detection with Transformers")
    b = _paper("Mediterranean Diet and Cardiovascular Risk")
    merged, stats = MergeService().merge([[a], [b]])
    assert len(merged) == 2
    assert stats.duplicates_removed == 0


def test_merge_keeps_better_abstract_and_higher_citations():
    a = _paper("Fusion for Disaster Assessment", abstract="short", citation_count=5)
    b = _paper("Fusion for Disaster Assessment", abstract="long " * 40, citation_count=99)
    merged, _ = MergeService().merge([[a], [b]])
    assert len(merged) == 1
    assert len(merged[0].abstract) > 100
    assert merged[0].citation_count == 99


def test_merge_unions_pdf_candidates_without_duplicates():
    a = _paper(
        "Multimodal Building Damage Assessment", doi="10.1/abc",
        pdf_candidates=[PdfCandidate("https://a.org/x.pdf", "OpenAlex")],
    )
    b = _paper(
        "Multimodal Building Damage Assessment", doi="10.1/abc",
        pdf_candidates=[
            PdfCandidate("https://a.org/x.pdf", "Unpaywall"),
            PdfCandidate("https://b.org/y.pdf", "Unpaywall"),
        ],
    )
    merged, _ = MergeService().merge([[a], [b]])
    assert len(merged[0].pdf_candidates) == 2


def test_merge_preserves_insertion_order_of_unique_items():
    a = _paper("Paper A")
    b = _paper("Paper B")
    c = _paper("Paper C")
    merged, _ = MergeService().merge([[a], [b], [c]])
    assert [p.title for p in merged] == ["Paper A", "Paper B", "Paper C"]


def test_dedupe_candidates_normalises_urls():
    cands = [
        PdfCandidate("https://a.org/x.pdf", "A"),
        PdfCandidate("https://a.org/x.pdf/", "B"),
        PdfCandidate("https://a.org/X.PDF", "C"),
        PdfCandidate("https://b.org/y.pdf", "D"),
    ]
    unique = dedupe_candidates(cands)
    assert len(unique) == 2
    assert unique[0].provider == "A"


def test_stats_text_is_human_readable():
    merged, stats = MergeService().merge([[_paper("One")], [_paper("One")]])
    text = stats.as_text()
    assert "1 条" not in text or "→" in text
    assert isinstance(text, str)
