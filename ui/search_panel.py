"""Left-hand search panel: mode, query, year range, venues, directions.

The filter groups live in a scroll area, while the primary actions are pinned to
the bottom of the panel so the search button is always reachable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from models.paper import SearchMode
from utils.config import Settings

MODE_OPTIONS = [
    (SearchMode.KEYWORDS, "关键词"),
    (SearchMode.TITLE, "论文标题"),
    (SearchMode.AUTHOR, "作者"),
    (SearchMode.DOI, "DOI"),
    (SearchMode.ARXIV_ID, "arXiv ID"),
    (SearchMode.TOPIC, "研究方向"),
]


class SearchPanel(QWidget):
    search_requested = Signal(dict)
    profile_requested = Signal()
    reset_requested = Signal()

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        self.scroll.setWidget(container)
        groups = QVBoxLayout(container)
        groups.setContentsMargins(0, 0, 6, 0)
        groups.setSpacing(10)

        self._profile_label = QLabel("方向：" + str(self.settings.profile.get("name") or ""))
        self._profile_label.setWordWrap(True)
        self._profile_label.setObjectName("AppSubtitle")
        groups.addWidget(self._profile_label)

        groups.addWidget(self._build_mode_group())
        groups.addWidget(self._build_year_group())
        groups.addWidget(self._build_venue_group())
        groups.addWidget(self._build_direction_group())
        groups.addStretch(1)

        layout.addWidget(self.scroll, 1)
        layout.addWidget(self._build_actions())

    # ------------------------------------------------------------------ #
    def _build_mode_group(self) -> QGroupBox:
        box = QGroupBox("检索条件")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        self.mode_combo = QComboBox()
        for mode, label in MODE_OPTIONS:
            self.mode_combo.addItem(label, mode.value)
        layout.addWidget(self.mode_combo)

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("输入标题 / 关键词 / 作者 / DOI / arXiv ID")
        self.query_edit.setClearButtonEnabled(True)
        self.query_edit.returnPressed.connect(self._emit_search)
        layout.addWidget(self.query_edit)

        hint = QLabel("提示：DOI 与 arXiv ID 模式会直接定位单篇论文")
        hint.setObjectName("AppSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("每个来源最多"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(5, 100)
        self.limit_spin.setValue(int(self.settings.search_cfg.get("default_limit", 30)))
        self.limit_spin.setSuffix(" 篇")
        limit_row.addWidget(self.limit_spin)
        layout.addLayout(limit_row)
        return box

    def _build_year_group(self) -> QGroupBox:
        box = QGroupBox("时间范围")
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.year_check = QCheckBox("限定年份")
        self.year_check.toggled.connect(self._on_year_toggled)
        row.addWidget(self.year_check)
        row.addStretch(1)
        layout.addLayout(row)

        self.year_from = QSpinBox()
        self.year_to = QSpinBox()
        current = datetime.now().year
        for spin in (self.year_from, self.year_to):
            spin.setRange(1900, current + 2)
            spin.setEnabled(False)
        self.year_from.setValue(max(1990, current - 3))
        self.year_to.setValue(current + 1)
        row2 = QHBoxLayout()
        row2.addWidget(self.year_from)
        row2.addWidget(QLabel("至"))
        row2.addWidget(self.year_to)
        layout.addLayout(row2)
        return box

    def _build_venue_group(self) -> QGroupBox:
        box = QGroupBox("会议 / 期刊")
        layout = QVBoxLayout(box)
        self.venue_list = QListWidget()
        self.venue_list.setMaximumHeight(150)
        for venue in self.settings.venues:
            item = QListWidgetItem(venue)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.venue_list.addItem(item)
        layout.addWidget(self.venue_list)

        buttons = QHBoxLayout()
        all_btn = QPushButton("全选")
        all_btn.clicked.connect(lambda: self._set_all(self.venue_list, Qt.Checked))
        none_btn = QPushButton("清空")
        none_btn.clicked.connect(lambda: self._set_all(self.venue_list, Qt.Unchecked))
        buttons.addWidget(all_btn)
        buttons.addWidget(none_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return box

    def _build_direction_group(self) -> QGroupBox:
        box = QGroupBox("研究方向过滤")
        layout = QVBoxLayout(box)
        self.direction_list = QListWidget()
        self.direction_list.setMaximumHeight(180)
        for direction in self.settings.directions:
            item = QListWidgetItem(direction)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.direction_list.addItem(item)
        layout.addWidget(self.direction_list)
        hint = QLabel("勾选后只保留标题/摘要中命中该方向的论文")
        hint.setObjectName("AppSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return box

    def _build_actions(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 4, 6, 0)
        layout.setSpacing(6)

        self.search_button = QPushButton("搜索论文")
        self.search_button.setObjectName("primary")
        self.search_button.setMinimumHeight(36)
        self.search_button.clicked.connect(self._emit_search)
        layout.addWidget(self.search_button)

        row = QHBoxLayout()
        self.profile_button = QPushButton("我的方向")
        self.profile_button.clicked.connect(self.profile_requested.emit)
        row.addWidget(self.profile_button)

        reset = QPushButton("重置条件")
        reset.clicked.connect(self.reset)
        row.addWidget(reset)
        layout.addLayout(row)
        return wrapper

    # ------------------------------------------------------------------ #
    @staticmethod
    def _set_all(widget: QListWidget, state) -> None:
        for row in range(widget.count()):
            widget.item(row).setCheckState(state)

    def _on_year_toggled(self, enabled: bool) -> None:
        self.year_from.setEnabled(enabled)
        self.year_to.setEnabled(enabled)

    @staticmethod
    def _checked(widget: QListWidget) -> list[str]:
        return [
            widget.item(row).text()
            for row in range(widget.count())
            if widget.item(row).checkState() == Qt.Checked
        ]

    def set_busy(self, busy: bool) -> None:
        self.search_button.setEnabled(not busy)
        self.search_button.setText("搜索中…" if busy else "搜索论文")

    def set_profile_name(self, name: str) -> None:
        self._profile_label.setText("方向：" + (name or "未设置"))

    def reset(self) -> None:
        self.query_edit.clear()
        self.mode_combo.setCurrentIndex(0)
        self.year_check.setChecked(False)
        self._set_all(self.venue_list, Qt.Unchecked)
        self._set_all(self.direction_list, Qt.Unchecked)
        self.limit_spin.setValue(int(self.settings.search_cfg.get("default_limit", 30)))
        self.reset_requested.emit()

    def current_query(self) -> dict:
        mode = self.mode_combo.currentData() or SearchMode.KEYWORDS.value
        text = self.query_edit.text().strip()
        year_from = self.year_from.value() if self.year_check.isChecked() else None
        year_to = self.year_to.value() if self.year_check.isChecked() else None
        return {
            "text": text,
            "mode": mode,
            "year_from": year_from,
            "year_to": year_to,
            "venues": self._checked(self.venue_list),
            "directions": self._checked(self.direction_list),
            "limit": self.limit_spin.value(),
        }

    def _emit_search(self) -> None:
        payload = self.current_query()
        if not payload["text"]:
            self.query_edit.setFocus()
            return
        self.search_requested.emit(payload)
