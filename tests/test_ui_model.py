"""Qt table model tests (offscreen, no window shown)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for UI tests")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from models.paper import OaStatus, PdfStatus, Paper  # noqa: E402
from ui.models import (  # noqa: E402
    PDF_LABELS,
    STATUS_LABELS,
    PaperFilterProxy,
    PaperTableModel,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_model_exposes_all_columns(qapp, sample_paper):
    model = PaperTableModel()
    model.set_papers([sample_paper])
    assert model.rowCount() == 1
    assert model.columnCount() == 11
    headers = [
        model.headerData(i, Qt.Horizontal) for i in range(model.columnCount())
    ]
    assert headers[:3] == ["状态", "标题", "年份"]
    assert "相关性" in headers


def test_model_display_values(qapp, sample_paper):
    model = PaperTableModel()
    model.set_papers([sample_paper])

    def cell(row, col):
        return model.data(model.index(row, col), Qt.DisplayRole)

    assert cell(0, 0) == STATUS_LABELS[sample_paper.status_label]
    assert cell(0, 1) == sample_paper.title
    assert cell(0, 2) == 2026
    assert cell(0, 4) == sample_paper.venue
    assert cell(0, 5) == sample_paper.doi
    assert cell(0, 6) == sample_paper.arxiv_id
    assert cell(0, 8) == "公开"
    assert cell(0, 9) == PDF_LABELS[PdfStatus.NOT_CHECKED.value]
    assert cell(0, 10) == ""


def test_model_local_checkmark(qapp, sample_paper):
    sample_paper.local_pdf_path = "C:/library/x.pdf"
    sample_paper.pdf_status = PdfStatus.VALIDATED
    model = PaperTableModel()
    model.set_papers([sample_paper])
    assert model.data(model.index(0, 10), Qt.DisplayRole) == "✔"
    assert model.data(model.index(0, 0), Qt.DisplayRole) == "已验证"


def test_model_tooltip_contains_score_explanation(qapp, sample_paper):
    sample_paper.relevance_score = 85
    sample_paper.relevance_band = "较相关"
    sample_paper.relevance_details = [
        {"field": "title", "label": "标题命中", "terms": ["change detection"], "points": 40}
    ]
    model = PaperTableModel()
    model.set_papers([sample_paper])
    tooltip = model.data(model.index(0, 7), Qt.ToolTipRole)
    assert "85" in tooltip
    assert "change detection" in tooltip


def test_proxy_text_filter(qapp, sample_paper):
    other = Paper(title="Mediterranean Diet Study", year=2020)
    model = PaperTableModel()
    model.set_papers([sample_paper, other])
    proxy = PaperFilterProxy()
    proxy.setSourceModel(model)
    assert proxy.rowCount() == 2
    proxy.set_text_filter("sar")
    assert proxy.rowCount() == 1


def test_proxy_only_local_and_oa(qapp, sample_paper):
    local = Paper(title="Local Paper", year=2025, local_pdf_path="C:/x.pdf",
                  oa_status=OaStatus.OPEN_ACCESS)
    closed = Paper(title="Closed Paper", year=2025, oa_status=OaStatus.CLOSED)
    model = PaperTableModel()
    model.set_papers([local, closed])
    proxy = PaperFilterProxy()
    proxy.setSourceModel(model)

    proxy.set_only_local(True)
    assert proxy.rowCount() == 1
    proxy.set_only_local(False)

    proxy.set_only_oa(True)
    assert proxy.rowCount() == 1
    proxy.set_only_oa(False)
    assert proxy.rowCount() == 2


def test_model_update_and_note(qapp, sample_paper):
    model = PaperTableModel()
    model.set_papers([sample_paper])
    sample_paper.pdf_status = PdfStatus.PUBLIC_BUT_BLOCKED
    model.update_paper(sample_paper)
    assert model.data(model.index(0, 0), Qt.DisplayRole) == "公开但被阻止"

    model.set_note(sample_paper, "403 blocked by publisher")
    tooltip = model.data(model.index(0, 0), Qt.ToolTipRole)
    assert "403" in tooltip
