"""Result table widget: filter bar, sortable table, context menu."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
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
    relax_requested = Signal(str)
    logs_requested = Signal()

    def set_sort_mode(self, mode: str) -> None:
        key = 'date' if mode == 'recent' else 'score'
        column = next(index for index, item in enumerate(COLUMNS) if item[0] == key)
        self.table.sortByColumn(column, Qt.DescendingOrder)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.model = PaperTableModel(self)
        self.proxy = PaperFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.feedback_box = QFrame()
        self.feedback_box.setObjectName('SearchFeedback')
        self.feedback_box.setStyleSheet('QFrame#SearchFeedback { background: #191f29; border: 1px solid #35465e; border-radius: 6px; }')
        feedback = QVBoxLayout(self.feedback_box)
        feedback.setContentsMargins(12,10,12,10)
        self.feedback_title = QLabel('输入关键词或选择方向 / 期刊，然后点击搜索')
        self.feedback_title.setStyleSheet('font-weight: 600; color: #9bc7ff;')
        self.feedback_title.setWordWrap(True)
        self.feedback_title.setTextFormat(Qt.PlainText)
        feedback.addWidget(self.feedback_title)
        self.feedback_detail = QLabel('搜索进度、来源错误和筛选原因会直接显示在这里。')
        self.feedback_detail.setWordWrap(True)
        self.feedback_detail.setTextFormat(Qt.PlainText)
        self.feedback_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        feedback.addWidget(self.feedback_detail)
        self.search_progress = QProgressBar()
        self.search_progress.setRange(0,0)
        self.search_progress.setMaximumHeight(8)
        self.search_progress.setTextVisible(False)
        self.search_progress.hide()
        feedback.addWidget(self.search_progress)
        actions = QHBoxLayout()
        self.relax_venue_button = QPushButton('取消期刊限制重搜')
        self.relax_direction_button = QPushButton('取消方向限制重搜')
        self.keyword_only_button = QPushButton('仅用关键词重搜')
        for button,key in ((self.relax_venue_button,'venue'),(self.relax_direction_button,'direction'),(self.keyword_only_button,'both')):
            button.clicked.connect(lambda _checked=False, key=key:self.relax_requested.emit(key))
            button.hide()
            actions.addWidget(button)
        self.logs_button = QPushButton('查看日志')
        self.logs_button.clicked.connect(self.logs_requested.emit)
        self.logs_button.hide()
        actions.addWidget(self.logs_button)
        actions.addStretch(1)
        feedback.addLayout(actions)
        layout.addWidget(self.feedback_box)

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
    def show_searching(self, query) -> None:
        self.set_papers([])
        self.feedback_title.setText('正在检索，请稍候…')
        self.feedback_detail.setText('检索词：' + (query.text or '仅方向 / 期刊条件') + '\n完成后会在此显示结果数量、实际检索词和来源状态。')
        self.search_progress.show()
        for button in (self.relax_venue_button,self.relax_direction_button,self.keyword_only_button,self.logs_button):
            button.hide()

    def show_outcome(self, outcome) -> None:
        self.set_papers(outcome.papers)
        self.search_progress.hide()
        self.feedback_title.setText(outcome.feedback_heading())
        self.feedback_detail.setText(outcome.feedback_text())
        empty = not outcome.papers
        venues = bool(outcome.query.venues)
        directions = bool(outcome.query.extra.get('directions'))
        self.relax_venue_button.setVisible(empty and venues)
        self.relax_direction_button.setVisible(empty and directions)
        self.keyword_only_button.setVisible(empty and venues and directions)
        self.logs_button.show()

    def show_failure(self, message: str) -> None:
        self.search_progress.hide()
        self.feedback_title.setText('检索失败')
        self.feedback_detail.setText(message + '\n详细错误可点击“查看日志”。')
        self.logs_button.show()

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
