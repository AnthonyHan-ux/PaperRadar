"""Qt table models for search results and the local library."""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor

from models.paper import OaStatus, PdfStatus, Paper
from utils.title_normalizer import snippet

STATUS_LABELS: dict[str, str] = {
    PdfStatus.NOT_CHECKED.value: "未检查",
    PdfStatus.SEARCHING.value: "查找中",
    PdfStatus.OPEN_ACCESS_FOUND.value: "有公开PDF",
    PdfStatus.DOWNLOADING.value: "下载中",
    PdfStatus.DOWNLOADED.value: "已下载",
    PdfStatus.VALIDATED.value: "已验证",
    PdfStatus.TITLE_WARNING.value: "标题待确认",
    PdfStatus.PUBLIC_BUT_BLOCKED.value: "公开但被阻止",
    PdfStatus.PAYWALLED_ONLY.value: "仅付费",
    PdfStatus.NO_PUBLIC_COPY_FOUND.value: "无公开副本",
    PdfStatus.MISSING_LOCAL_FILE.value: "本地文件丢失",
    PdfStatus.DOWNLOAD_FAILED.value: "下载失败",
    PdfStatus.WRONG_PAPER.value: "错误论文",
    PdfStatus.INVALID_PDF.value: "无效PDF",
}

STATUS_COLORS: dict[str, str] = {
    PdfStatus.VALIDATED.value: "#7ee787",
    PdfStatus.TITLE_WARNING.value: "#ffd166",
    PdfStatus.DOWNLOADED.value: "#7ee787",
    PdfStatus.OPEN_ACCESS_FOUND.value: "#79c0ff",
    PdfStatus.SEARCHING.value: "#79c0ff",
    PdfStatus.DOWNLOADING.value: "#79c0ff",
    PdfStatus.PUBLIC_BUT_BLOCKED.value: "#ffa657",
    PdfStatus.PAYWALLED_ONLY.value: "#ff9b8f",
    PdfStatus.DOWNLOAD_FAILED.value: "#ff7b72",
    PdfStatus.WRONG_PAPER.value: "#ff7b72",
    PdfStatus.INVALID_PDF.value: "#ff7b72",
    PdfStatus.NO_PUBLIC_COPY_FOUND.value: "#8b949e",
    PdfStatus.MISSING_LOCAL_FILE.value: "#ffd166",
    PdfStatus.NOT_CHECKED.value: "#8b949e",
}

OA_LABELS = {
    OaStatus.OPEN_ACCESS.value: "公开",
    OaStatus.FREE_TO_READ.value: "免费阅读",
    OaStatus.CLOSED.value: "仅付费",
    OaStatus.UNKNOWN.value: "未知",
}

PDF_LABELS = {
    PdfStatus.NOT_CHECKED.value: "未检查",
    PdfStatus.SEARCHING.value: "查找中",
    PdfStatus.OPEN_ACCESS_FOUND.value: "可下载",
    PdfStatus.DOWNLOADING.value: "下载中",
    PdfStatus.DOWNLOADED.value: "已下载",
    PdfStatus.VALIDATED.value: "已验证",
    PdfStatus.TITLE_WARNING.value: "标题待确认",
    PdfStatus.PUBLIC_BUT_BLOCKED.value: "被阻止",
    PdfStatus.PAYWALLED_ONLY.value: "仅付费",
    PdfStatus.NO_PUBLIC_COPY_FOUND.value: "无公开版",
    PdfStatus.MISSING_LOCAL_FILE.value: "文件丢失",
    PdfStatus.DOWNLOAD_FAILED.value: "下载失败",
    PdfStatus.WRONG_PAPER.value: "错误论文",
    PdfStatus.INVALID_PDF.value: "无效PDF",
}

COLUMNS = [
    ("status", "状态", 78),
    ("title", "标题", 380),
    ("year", "年份", 64),
    ("authors", "作者", 110),
    ("venue", "期刊/会议", 122),
    ("doi", "DOI", 120),
    ("arxiv", "arXiv", 76),
    ("score", "相关性", 76),
    ("oa", "OA", 56),
    ("pdf", "PDF状态", 78),
    ("local", "本地", 42),
]

SORT_ROLE = Qt.UserRole + 1
PAPER_ROLE = Qt.UserRole + 2


class PaperTableModel(QAbstractTableModel):
    def __init__(self, parent=None, show_score: bool = True) -> None:
        super().__init__(parent)
        self._papers: list[Paper] = []
        self._notes: dict[int, str] = {}
        self.show_score = show_score
        self._columns = [c for c in COLUMNS if show_score or c[0] != "score"]

    # ------------------------------------------------------------------ #
    def set_papers(self, papers: Iterable[Paper]) -> None:
        self.beginResetModel()
        self._papers = list(papers)
        self._notes.clear()
        self.endResetModel()

    def append_papers(self, papers: Iterable[Paper]) -> None:
        new = list(papers)
        if not new:
            return
        start = len(self._papers)
        self.beginInsertRows(QModelIndex(), start, start + len(new) - 1)
        self._papers.extend(new)
        self.endInsertRows()

    def update_paper(self, paper: Paper) -> None:
        for row, existing in enumerate(self._papers):
            if existing is paper or (paper.id and existing.id == paper.id and
                                     existing.title == paper.title):
                self._papers[row] = paper
                left = self.index(row, 0)
                right = self.index(row, self.columnCount() - 1)
                self.dataChanged.emit(left, right)
                return

    def set_note(self, paper: Paper, note: str) -> None:
        self._notes[id(paper)] = note
        for row, existing in enumerate(self._papers):
            if existing is paper:
                index = self.index(row, 0)
                self.dataChanged.emit(index, self.index(row, self.columnCount() - 1))
                return

    def paper_at(self, row: int) -> Optional[Paper]:
        if 0 <= row < len(self._papers):
            return self._papers[row]
        return None

    def papers(self) -> list[Paper]:
        return list(self._papers)

    # ------------------------------------------------------------------ #
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._papers)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self._columns):
            return self._columns[section][1]
        if orientation == Qt.Vertical:
            return str(section + 1)
        return None

    def _value(self, paper: Paper, key: str):
        if key == "status":
            return STATUS_LABELS.get(paper.status_label, paper.status_label)
        if key == "title":
            return paper.title
        if key == "year":
            return paper.year or ""
        if key == "authors":
            return paper.authors_short
        if key == "venue":
            return paper.venue or ""
        if key == "doi":
            return paper.doi or ""
        if key == "arxiv":
            return paper.arxiv_id or ""
        if key == "score":
            return "" if paper.relevance_score is None else f"{paper.relevance_score:.0f}"
        if key == "oa":
            return OA_LABELS.get(paper.oa_status.value, paper.oa_status.value)
        if key == "pdf":
            return PDF_LABELS.get(paper.pdf_status.value, paper.pdf_status.value)
        if key == "local":
            return "✔" if paper.local_pdf_path else ""
        return ""

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        paper = self._papers[index.row()]
        key = self._columns[index.column()][0]

        if role == Qt.DisplayRole:
            return self._value(paper, key)
        if role == PAPER_ROLE:
            return paper
        if role == SORT_ROLE:
            if key == "year":
                return paper.year or 0
            if key == "score":
                return paper.relevance_score if paper.relevance_score is not None else -1
            return str(self._value(paper, key) or "")
        if role == Qt.TextAlignmentRole:
            if key in ("year", "score", "local", "arxiv"):
                return int(Qt.AlignCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            if key == "status":
                return QColor(STATUS_COLORS.get(paper.status_label, "#c9d1d9"))
            if key == "score":
                score = paper.relevance_score or 0
                if score >= 90:
                    return QColor("#7ee787")
                if score >= 70:
                    return QColor("#79c0ff")
                if score >= 50:
                    return QColor("#ffd166")
                return QColor("#8b949e")
            if key == "local":
                return QColor("#7ee787") if paper.local_pdf_path else QColor("#484f58")
            if key == "oa":
                return QColor("#7ee787") if paper.oa_flag else QColor("#8b949e")
            if key == "title":
                return QColor("#c9d1d9") if not paper.local_pdf_path else QColor("#e6edf3")
            return None
        if role == Qt.ToolTipRole:
            return self._tooltip(paper, key)
        return None

    def _tooltip(self, paper: Paper, key: str) -> str:
        note = self._notes.get(id(paper), "")
        if key == "title":
            parts = [paper.title]
            if paper.abstract:
                parts.append("")
                parts.append(snippet(paper.abstract, 320))
            if paper.sources:
                parts.append("")
                parts.append("来源：" + ", ".join(paper.sources))
            return "\n".join(parts)
        if key == "status":
            lines = [STATUS_LABELS.get(paper.status_label, paper.status_label)]
            if note:
                lines.append(note)
            if paper.local_pdf_path:
                lines.append(paper.local_pdf_path)
            return "\n".join(lines)
        if key == "score":
            if not paper.relevance_details:
                return f"相关性 {paper.relevance_score or 0:.0f}（{paper.relevance_band}）\n未命中任何关键词"
            lines = [f"相关性 {paper.relevance_score or 0:.0f}（{paper.relevance_band}）", ""]
            for item in paper.relevance_details:
                terms = "、".join(item.get("terms", [])[:6])
                lines.append(f"{item.get('label', '')}：{terms}  {'+' if item.get('points', 0) >= 0 else ''}{item.get('points', 0):.0f}")
            return "\n".join(lines)
        if key == "doi" and paper.doi:
            return f"https://doi.org/{paper.doi}"
        if key == "arxiv" and paper.arxiv_id:
            return f"https://arxiv.org/abs/{paper.arxiv_id}"
        if key == "venue":
            return paper.venue or ""
        if key == "pdf":
            lines = [PDF_LABELS.get(paper.pdf_status.value, paper.pdf_status.value)]
            if note:
                lines.append(note)
            if paper.pdf_urls:
                lines.append("")
                lines.extend(paper.pdf_urls[:4])
            return "\n".join(lines)
        if key == "local" and paper.local_pdf_path:
            return paper.local_pdf_path
        if key == "oa":
            return OA_LABELS.get(paper.oa_status.value, paper.oa_status.value)
        return ""


class PaperFilterProxy(QSortFilterProxyModel):
    """Sorting + optional text/state filtering for the result table."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)
        self._text = ""
        self._only_oa = False
        self._only_local = False

    def set_text_filter(self, text: str) -> None:
        self._text = (text or "").strip().lower()
        self.invalidateFilter()

    def set_only_oa(self, enabled: bool) -> None:
        self._only_oa = bool(enabled)
        self.invalidateFilter()

    def set_only_local(self, enabled: bool) -> None:
        self._only_local = bool(enabled)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if not isinstance(model, PaperTableModel):
            return True
        paper = model.paper_at(source_row)
        if paper is None:
            return False
        if self._only_oa and not (paper.oa_flag or paper.pdf_candidates):
            return False
        if self._only_local and not paper.local_pdf_path:
            return False
        if self._text:
            haystack = " ".join(
                filter(None, [paper.title, paper.authors_short, paper.venue or "",
                              paper.doi or "", paper.arxiv_id or "",
                              (paper.abstract or "")[:600]])
            ).lower()
            if self._text not in haystack:
                return False
        return True

    def paper_at_proxy(self, proxy_index: QModelIndex) -> Optional[Paper]:
        model = self.sourceModel()
        if not isinstance(model, PaperTableModel) or not proxy_index.isValid():
            return None
        source = self.mapToSource(proxy_index)
        return model.paper_at(source.row())
