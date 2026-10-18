"""Log panel: mirrors ``logs/app.log`` into the GUI, thread-safely."""

from __future__ import annotations

import html
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from utils.logger import QueueLogHandler

LEVEL_COLORS = {
    "DEBUG": "#6e7681",
    "INFO": "#9fb0c7",
    "WARNING": "#ffd166",
    "ERROR": "#ff7b72",
    "CRITICAL": "#ff7b72",
}


class LogPanel(QWidget):
    def __init__(self, handler: Optional[QueueLogHandler] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.handler = handler
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        title = QLabel("运行日志")
        title.setObjectName("SectionTitle")
        toolbar.addWidget(title)
        toolbar.addStretch(1)

        self.autoscroll_btn = QPushButton("自动滚动：开")
        self.autoscroll_btn.setCheckable(True)
        self.autoscroll_btn.setChecked(True)
        self.autoscroll_btn.toggled.connect(
            lambda on: self.autoscroll_btn.setText("自动滚动：" + ("开" if on else "关"))
        )
        toolbar.addWidget(self.autoscroll_btn)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(lambda: self.output.clear())
        toolbar.addWidget(clear_btn)

        open_btn = QPushButton("打开日志目录")
        open_btn.clicked.connect(self._open_log_dir)
        toolbar.addWidget(open_btn)
        layout.addLayout(toolbar)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(6000)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setPlaceholderText("日志会显示在这里，同时写入 logs/app.log")
        layout.addWidget(self.output, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._drain)
        self._timer.start()

        self._log_dir = None

    # ------------------------------------------------------------------ #
    def set_log_dir(self, path) -> None:
        self._log_dir = path

    def _open_log_dir(self) -> None:
        import os

        if self._log_dir:
            try:
                os.startfile(str(self._log_dir))  # noqa: S606
            except OSError:
                pass

    def _drain(self) -> None:
        if self.handler is None:
            return
        records = self.handler.drain()
        if not records:
            return
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        for level, message in records:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(LEVEL_COLORS.get(level, "#9fb0c7")))
            cursor.insertText(message + "\n", fmt)
        if self.autoscroll_btn.isChecked():
            self.output.verticalScrollBar().setValue(
                self.output.verticalScrollBar().maximum()
            )

    def append_line(self, message: str, color: str = "#9fb0c7") -> None:
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(message + "\n", fmt)
        if self.autoscroll_btn.isChecked():
            self.output.verticalScrollBar().setValue(
                self.output.verticalScrollBar().maximum()
            )

    def stop(self) -> None:
        self._timer.stop()
