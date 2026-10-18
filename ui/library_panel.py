"""Local library tab: browse what is already in the database / on disk."""

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
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from models.paper import Paper
from ui.models import COLUMNS, PaperFilterProxy, PaperTableModel


class LibraryPanel(QWidget):
    open_pdf_requested = Signal(object)
    open_folder_requested = Signal(object)
    refresh_requested = Signal(str, bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.model = PaperTableModel(self)
        self.proxy = PaperFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("在本地文献库中搜索（标题/作者/摘要）")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self._emit_refresh)
        toolbar.addWidget(self.search_edit, 1)

        self.only_local_check = QCheckBox("只看已下载 PDF")
        self.only_local_check.toggled.connect(lambda _: self._emit_refresh())
        toolbar.addWidget(self.only_local_check)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._emit_refresh)
        toolbar.addWidget(refresh_btn)

        open_btn = QPushButton("打开 PDF")
        open_btn.clicked.connect(self._open_selected)
        toolbar.addWidget(open_btn)

        folder_btn = QPushButton("打开所在文件夹")
        folder_btn.clicked.connect(self._open_folder)
        toolbar.addWidget(folder_btn)

        self.count_label = QLabel("0 篇")
        self.count_label.setObjectName("Counter")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setWordWrap(False)
        self.table.doubleClicked.connect(lambda _idx: self._open_selected())
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column, (_key, _label, width) in enumerate(COLUMNS):
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.sortByColumn(2, Qt.DescendingOrder)
        layout.addWidget(self.table, 1)

    # ------------------------------------------------------------------ #
    def set_papers(self, papers: list[Paper]) -> None:
        self.model.set_papers(papers)
        self.count_label.setText(f"{len(papers)} 篇")

    def _emit_refresh(self) -> None:
        self.refresh_requested.emit(
            self.search_edit.text().strip(), self.only_local_check.isChecked()
        )

    def _selected(self) -> Optional[Paper]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.proxy.paper_at_proxy(rows[0])

    def _open_selected(self) -> None:
        paper = self._selected()
        if paper is not None:
            self.open_pdf_requested.emit(paper)

    def _open_folder(self) -> None:
        paper = self._selected()
        if paper is not None:
            self.open_folder_requested.emit(paper)
