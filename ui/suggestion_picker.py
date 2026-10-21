"""Editable suggestion dropdowns with contains-completion and multi-select."""
from __future__ import annotations

from PySide6.QtCore import Qt, QStringListModel
from PySide6.QtWidgets import (QComboBox, QCompleter, QHBoxLayout, QLabel,
                              QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)
from utils.search_catalog import resolve_term


class SuggestionCombo(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setMaxVisibleItems(12)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(12)
        self.lineEdit().setClearButtonEnabled(True)
        self._options = []

    def set_options(self, options) -> None:
        text = self.currentText()
        previous = self.blockSignals(True)
        try:
            self.clear()
            self._options = list(options)
            completions = []
            for item in self._options:
                self.addItem(item['label'], item['value'])
                self.setItemData(self.count()-1, item['label'], Qt.ToolTipRole)
                completions.extend([item['label'], *item.get('aliases', [])])
            self.setCurrentIndex(-1)
            self.setEditText(text)
            completer = QCompleter(self)
            completer.setModel(QStringListModel(list(dict.fromkeys(completions)), completer))
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            completer.setCompletionMode(QCompleter.PopupCompletion)
            completer.setMaxVisibleItems(12)
            self.setCompleter(completer)
        finally:
            self.blockSignals(previous)

    def value(self) -> str:
        text = self.currentText().strip()
        for item in self._options:
            if text.casefold() in {str(alias).casefold() for alias in [item['label'], item['value'], *item.get('aliases', [])]}:
                return item['value']
        return text


class MultiValuePicker(QWidget):
    def __init__(self, kind: str, placeholder: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.combo = SuggestionCombo()
        self.combo.lineEdit().setPlaceholderText(placeholder)
        row.addWidget(self.combo,1)
        add = QPushButton('加入')
        add.setMaximumWidth(58)
        add.clicked.connect(self.add_current)
        row.addWidget(add)
        layout.addLayout(row)
        self.selected = QListWidget()
        self.selected.setMaximumHeight(80)
        self.selected.hide()
        self.selected.itemDoubleClicked.connect(self._remove)
        layout.addWidget(self.selected)
        hint_row = QHBoxLayout()
        self.hint = QLabel('可多选，最多 5 项；双击已选项移除')
        self.hint.setObjectName('AppSubtitle')
        self.hint.setWordWrap(True)
        hint_row.addWidget(self.hint,1)
        clear = QPushButton('清空')
        clear.setMaximumWidth(58)
        clear.setMaximumHeight(24)
        clear.clicked.connect(self.clear)
        hint_row.addWidget(clear)
        layout.addLayout(hint_row)
        self.combo.activated.connect(lambda _index: self.add_current())
        self.combo.lineEdit().returnPressed.connect(self.add_current)

    def set_options(self, options) -> None:
        self.combo.set_options(options)

    def add_current(self) -> None:
        for term in self._terms(self.combo.value()):
            self.add_value(term)
        self.combo.setCurrentIndex(-1)
        self.combo.clearEditText()

    def add_value(self, value: str) -> None:
        value = resolve_term(value, self.kind)
        if not value or value.casefold() in {term.casefold() for term in self.values(include_pending=False)}:
            return
        if self.selected.count() >= 5:
            self.hint.setText('已选满 5 项，请先移除一项。')
            return
        label = next((item['label'] for item in self.combo._options if item['value']==value), value)
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole,value)
        item.setToolTip(label + '\n双击移除')
        self.selected.addItem(item)
        self.selected.setFixedHeight(min(3,self.selected.count())*25+6)
        self.selected.show()

    def _remove(self, item) -> None:
        self.selected.takeItem(self.selected.row(item))
        self.selected.setFixedHeight(min(3,self.selected.count())*25+6)
        self.selected.setVisible(self.selected.count()>0)
        self.hint.setText('可多选，最多 5 项；双击已选项移除')

    @staticmethod
    def _terms(text: str) -> list[str]:
        return [term.strip() for term in text.replace('；',';').split(';') if term.strip()]

    def values(self, include_pending: bool = True) -> list[str]:
        values = [self.selected.item(index).data(Qt.UserRole) for index in range(self.selected.count())]
        if include_pending:
            values.extend(resolve_term(term,self.kind) for term in self._terms(self.combo.value()))
        return list(dict.fromkeys(values))

    def clear(self) -> None:
        self.selected.clear()
        self.selected.hide()
        self.combo.setCurrentIndex(-1)
        self.combo.clearEditText()
        self.hint.setText('可多选，最多 5 项；双击已选项移除')
