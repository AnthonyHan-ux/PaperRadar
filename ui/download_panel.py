"""Download queue panel: per-paper status, aggregate progress, cancel."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.paper import PdfStatus
from services.download_service import DownloadResult
from ui.models import PDF_LABELS

STATUS_COLORS = {
    PdfStatus.VALIDATED: "#7ee787",
    PdfStatus.TITLE_WARNING: "#ffd166",
    PdfStatus.PUBLIC_BUT_BLOCKED: "#ffa657",
    PdfStatus.PAYWALLED_ONLY: "#ff9b8f",
    PdfStatus.NO_PUBLIC_COPY_FOUND: "#8b949e",
    PdfStatus.DOWNLOAD_FAILED: "#ff7b72",
    PdfStatus.WRONG_PAPER: "#ff7b72",
    PdfStatus.INVALID_PDF: "#ff7b72",
}


class DownloadPanel(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        self.summary = QLabel("尚未开始下载")
        self.summary.setObjectName("SectionTitle")
        toolbar.addWidget(self.summary, 1)

        self.cancel_btn = QPushButton("取消下载")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        toolbar.addWidget(self.cancel_btn)

        clear_btn = QPushButton("清空列表")
        clear_btn.clicked.connect(self.clear)
        toolbar.addWidget(clear_btn)
        layout.addLayout(toolbar)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        layout.addWidget(self.progress)

        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        layout.addWidget(self.list, 1)

        self._total = 0
        self._finished = 0
        self._bytes = 0

    # ------------------------------------------------------------------ #
    def clear(self) -> None:
        self.list.clear()
        self.progress.setValue(0)
        self.summary.setText("尚未开始下载")
        self._total = 0
        self._finished = 0
        self._bytes = 0

    def start(self, total: int) -> None:
        self._total = max(1, total)
        self._finished = 0
        self._bytes = 0
        self.progress.setValue(0)
        self.summary.setText(f"正在处理 0 / {total} 篇")
        self.cancel_btn.setEnabled(True)

    def update_progress(self, title: str, done: int, total: int) -> None:
        if total <= 0:
            # OA resolution phase: no byte counts yet, just show what is happening.
            self.summary.setText(f"正在查找公开全文：{title[:48]}…")
            return
        self._bytes = done
        percent = int(min(100, done * 100 / total))
        self.progress.setValue(percent)
        self.summary.setText(
            f"下载中：{title[:48]}… {done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB"
        )

    def add_result(self, result: DownloadResult) -> None:
        self._finished += 1
        label = PDF_LABELS.get(result.status.value, result.status.value)
        item = QListWidgetItem(f"[{label}] {result.title[:80]}")
        item.setToolTip(f"{result.message}\n{result.used_url}")
        color = STATUS_COLORS.get(result.status)
        if color:
            from PySide6.QtGui import QColor

            item.setForeground(QColor(color))
        self.list.addItem(item)
        self.list.scrollToBottom()

        if self._total:
            self.progress.setValue(int(min(100, self._finished * 100 / self._total)))
        if self._finished >= self._total:
            self.finish()

    def finish(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.summary.setText(f"完成：共 {self._finished} 篇")
        self.progress.setValue(100)
