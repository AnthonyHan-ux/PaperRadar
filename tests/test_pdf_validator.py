"""PDF validation tests (real PDFs generated with PyMuPDF)."""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF is required")

from models.paper import OaStatus, Paper, PdfStatus  # noqa: E402
from services.pdf_validator import PdfValidator  # noqa: E402


def make_pdf(path, *texts: str, pages: int = 1):
    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        text = texts[index] if index < len(texts) else (texts[0] if texts else "")
        page.insert_textbox(
            fitz.Rect(50, 50, 545, 780), text, fontsize=12,
        )
    document.save(str(path))
    document.close()
    return path


@pytest.fixture()
def validator():
    return PdfValidator(min_bytes=200, title_threshold=90, warning_threshold=80)


def test_valid_pdf_is_verified(tmp_path, validator, sample_paper):
    path = make_pdf(
        tmp_path / "good.pdf",
        "Beyond Attention: Convolutional Global Context for Remote Sensing "
        "Change Detection\n\nWei Zhang, Li Wang, Ming Chen\n\nAbstract: ...",
    )
    result = validator.validate(path, sample_paper)
    assert result.status == PdfStatus.VALIDATED, result.reason
    assert result.title_similarity >= 90
    assert result.page_count == 1
    assert result.has_text


def test_html_saved_as_pdf_is_rejected(tmp_path, validator, sample_paper):
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"<html><body>403 Forbidden</body></html>")
    result = validator.validate(path, sample_paper)
    assert result.status == PdfStatus.INVALID_PDF
    assert "PDF" in result.reason


def test_truncated_pdf_is_rejected(tmp_path, validator, sample_paper):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"x" * 5000)
    result = validator.validate(path, sample_paper)
    assert result.status in (PdfStatus.INVALID_PDF, PdfStatus.WRONG_PAPER)


def test_too_small_file_is_rejected(tmp_path, validator, sample_paper):
    path = tmp_path / "tiny.pdf"
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    result = validator.validate(path, sample_paper)
    assert result.status == PdfStatus.INVALID_PDF
    assert "字节" in result.reason or "低于" in result.reason


def test_wrong_paper_is_flagged(tmp_path, validator, sample_paper):
    path = make_pdf(
        tmp_path / "other.pdf",
        "A Randomized Trial of Mediterranean Diet and Cardiovascular Outcomes "
        "in Elderly Patients",
    )
    result = validator.validate(path, sample_paper)
    assert result.status == PdfStatus.WRONG_PAPER, result.reason
    assert result.title_similarity < 80


def test_scanned_pdf_without_text_layer_warns(tmp_path, validator, sample_paper):
    document = fitz.open()
    page = document.new_page()
    page.draw_rect(fitz.Rect(100, 100, 400, 400), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    path = tmp_path / "scan.pdf"
    document.save(str(path))
    document.close()
    result = validator.validate(path, sample_paper)
    assert result.status == PdfStatus.TITLE_WARNING
    assert "文本层" in result.reason


def test_doi_does_not_override_unrelated_title(tmp_path, validator):
    paper = Paper(
        title="Optical SAR Change Detection with Robust Fusion",
        doi="10.1109/TGRS.2025.123456",
        year=2025,
    )
    path = make_pdf(
        tmp_path / "doi.pdf",
        "Some unrelated heading text\nDOI: 10.1109/TGRS.2025.123456\nmore text",
    )
    result = validator.validate(path, paper)
    assert result.status == PdfStatus.WRONG_PAPER


def test_missing_file_reports_invalid(tmp_path, validator, sample_paper):
    result = validator.validate(tmp_path / "nope.pdf", sample_paper)
    assert result.status == PdfStatus.INVALID_PDF


def test_header_probe(tmp_path, validator):
    good = tmp_path / "good.pdf"
    good.write_bytes(b"%PDF-1.4\n...")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"GIF89a")
    assert validator.check_header(good)
    assert not validator.check_header(bad)
