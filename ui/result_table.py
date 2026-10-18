"""Result table widget: filter bar, sortable table, context menu."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from models.paper import Paper
from ui.models import COLUMNS, PaperFilterProxy, PaperTableModel


class ResultTable(QWidget):
    selection_changed = Signal(int)
    open_source_requested = Signal(object)
    open_pdf_requested = Signal(object)
    download_requested = Signal(object)
    details_requested = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.model = PaperTableModel(self)
        self.proxy = PaperFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("在当前结果中筛选（标题/作者/期刊/DOI）")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self.proxy.set_text_filter)
        toolbar.addWidget(self.filter_edit, 1)

        self.only_oa = QCheckBox("只看公开")
        self.only_oa.toggled.connect(self.proxy.set_only_oa)
        toolbar.addWidget(self.only_oa)

        self.only_local = QCheckBox("只看本地")
        self.only_local.toggled.connect(self.proxy.set_only_local)
        toolbar.addWidget(self.only_local)

        self.count_label = QLabel("0 篇")
        self.count_label.setObjectName("Counter")
        toolbar.addSpacing(8)
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.doubleClicked.connect(self._on_double_click)

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        header.setSectionsMovable(False)
        for column, (_key, _label, width) in enumerate(COLUMNS):
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.sortByColumn(7, Qt.DescendingOrder)

        self.table.selectionModel().selectionChanged.connect(self._emit_selection)
        layout.addWidget(self.table, 1)

    # ------------------------------------------------------------------ #
    def set_papers(self, papers: list[Paper]) -> None:
        self.model.set_papers(papers)
        self._update_count()

    def papers(self) -> list[Paper]:
        return self.model.papers()

    def update_paper(self, paper: Paper) -> None:
        self.model.update_paper(paper)
        self._update_count()

    def set_note(self, paper: Paper, note: str) -> None:
        self.model.set_note(paper, note)

    def selected_papers(self) -> list[Paper]:
        rows = self.table.selectionModel().selectedRows()
        papers: list[Paper] = []
        for index in rows:
            paper = self.proxy.paper_at_proxy(index)
            if paper is not None:
                papers.append(paper)
        return papers

    def select_all(self) -> None:
        self.table.selectAll()

    def _emit_selection(self, *_args) -> None:
        self.selection_changed.emit(len(self.selected_papers()))

    def _update_count(self) -> None:
        total = len(self.model.papers())
        visible = self.proxy.rowCount()
        if visible == total:
            self.count_label.setText(f"{total} 篇")
        else:
            self.count_label.setText(f"{visible} / {total} 篇")

    def _on_double_click(self, index) -> None:
        paper = self.proxy.paper_at_proxy(index)
        if paper is None:
            return
        if paper.local_pdf_path:
            self.open_pdf_requested.emit(paper)
        else:
            self.open_source_requested.emit(paper)

    def _show_context_menu(self, position) -> None:
        papers = self.selected_papers()
        if not papers:
            return
        menu = QMenu(self)
        single = len(papers) == 1

        if single:
            menu.addAction("打开来源页", lambda: self.open_source_requested.emit(papers[0]))
        if single and papers[0].local_pdf_path:
            menu.addAction("打开本地 PDF", lambda: self.open_pdf_requested.emit(papers[0]))
        menu.addAction("下载公开 PDF", lambda: self.download_requested.emit(papers))
        if single:
            menu.addAction("查看详情", lambda: self.details_requested.emit(papers[0]))
        menu.addSeparator()
        menu.addAction("复制标题", lambda: self._copy_titles(papers))
        menu.exec(self.table.viewport().mapToGlobal(position))

    @staticmethod
    def _copy_titles(papers: list[Paper]) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText("\n".join(p.title for p in papers))
