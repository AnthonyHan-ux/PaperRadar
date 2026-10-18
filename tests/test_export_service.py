"""Export (CSV / BibTeX / RIS) tests."""

from __future__ import annotations

import csv

from models.paper import OaStatus, PdfStatus, Paper
from services.export_service import ExportService


def _paper(**kwargs) -> Paper:
    defaults = dict(
        title="Optical SAR Change Detection with Robust Fusion",
        authors=["Wei Zhang", "Li Wang"],
        year=2025,
        venue="IEEE Transactions on Geoscience and Remote Sensing",
        doi="10.1109/TGRS.2025.123456",
        arxiv_id="2501.01234",
        abstract="We study robust fusion under fog.",
        keywords=["change detection"],
        oa_status=OaStatus.OPEN_ACCESS,
        pdf_status=PdfStatus.VALIDATED,
        local_pdf_path="C:/library/2025/TGRS/x.pdf",
        relevance_score=87.5,
        sources=["openalex", "arxiv"],
    )
    defaults.update(kwargs)
    return Paper(**defaults)


def test_csv_export_has_bom_and_expected_columns(tmp_path):
    path = tmp_path / "out.csv"
    ExportService().to_csv([_paper()], path)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    row = rows[0]
    assert row["title"].startswith("Optical SAR")
    assert row["authors"] == "Wei Zhang; Li Wang"
    assert row["doi"] == "10.1109/tgrs.2025.123456"
    assert row["relevance_score"] == "87.5"
    assert row["local_path"].endswith("x.pdf")


def test_bibtex_export_structure(tmp_path):
    path = tmp_path / "out.bib"
    ExportService().to_bibtex([_paper()], path)
    text = path.read_text(encoding="utf-8")
    assert "@article{" in text
    assert "title = {" in text
    assert "author = {Wei Zhang and Li Wang}" in text
    assert "doi = {10.1109/tgrs.2025.123456}" in text
    assert "eprint = {2501.01234}" in text
    assert "archivePrefix = {arXiv}" in text


def test_bibtex_uses_inproceedings_for_conferences(tmp_path):
    paper = _paper(venue="CVPR 2025", doi=None, arxiv_id=None)
    path = tmp_path / "conf.bib"
    ExportService().to_bibtex([paper], path)
    text = path.read_text(encoding="utf-8")
    assert "@inproceedings{" in text
    assert "booktitle = {CVPR 2025}" in text


def test_bibtex_keys_are_unique(tmp_path):
    papers = [
        _paper(title="Same Year Same Author One"),
        _paper(title="Same Year Same Author Two"),
    ]
    path = tmp_path / "dup.bib"
    ExportService().to_bibtex(papers, path)
    text = path.read_text(encoding="utf-8")
    keys = [line.split("{")[1].rstrip(",") for line in text.splitlines() if line.startswith("@")]
    assert len(keys) == len(set(keys))


def test_bibtex_escapes_special_characters(tmp_path):
    paper = _paper(title="Cloud & Fog Removal: 100% Robust_v2")
    path = tmp_path / "esc.bib"
    ExportService().to_bibtex([paper], path)
    text = path.read_text(encoding="utf-8")
    assert r"Cloud \& Fog Removal: 100\% Robust\_v2" in text


def test_ris_export_structure(tmp_path):
    path = tmp_path / "out.ris"
    ExportService().to_ris([_paper()], path)
    text = path.read_text(encoding="utf-8")
    assert "TY  - JOUR" in text
    assert "TI  - Optical SAR Change Detection" in text
    assert "AU  - Wei Zhang" in text
    assert "DO  - 10.1109/tgrs.2025.123456" in text
    assert "ER  -" in text


def test_export_dispatch_and_unknown_format(tmp_path):
    service = ExportService()
    assert service.export([_paper()], "csv", tmp_path / "a.csv").exists()
    assert service.export([_paper()], "bib", tmp_path / "a.bib").exists()
    assert service.export([_paper()], "ris", tmp_path / "a.ris").exists()

    import pytest

    with pytest.raises(ValueError):
        service.export([_paper()], "docx", tmp_path / "a.docx")


def test_default_filename_has_extension():
    service = ExportService()
    assert service.default_filename("csv").endswith(".csv")
    assert service.default_filename("bib").endswith(".bib")
