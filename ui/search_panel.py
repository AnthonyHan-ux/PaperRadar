"""Search suggestions, editable filters and a day-precision calendar range."""
from __future__ import annotations

from typing import Optional
from PySide6.QtCore import QDate, QLocale, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from models.paper import SearchMode
from utils.config import Settings
from utils.search_catalog import load_catalog, suggestions
from ui.suggestion_picker import MultiValuePicker, SuggestionCombo

MODE_OPTIONS = [(SearchMode.KEYWORDS,'关键词'),(SearchMode.TITLE,'论文标题'),
    (SearchMode.AUTHOR,'作者'),(SearchMode.DOI,'DOI'),(SearchMode.ARXIV_ID,'arXiv ID'),(SearchMode.TOPIC,'研究方向')]


class SearchPanel(QWidget):
    search_requested = Signal(dict)
    profile_requested = Signal()
    reset_requested = Signal()

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(8)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        self.scroll.setWidget(container)
        groups = QVBoxLayout(container)
        groups.setContentsMargins(0,0,6,0)
        groups.setSpacing(8)
        self._profile_label = QLabel('我的项目：' + str(settings.profile.get('name') or '未设置'))
        self._profile_label.setObjectName('AppSubtitle')
        self._profile_label.setWordWrap(True)
        groups.addWidget(self._profile_label)
        groups.addWidget(self._build_mode_group())
        groups.addWidget(self._build_directions())
        groups.addWidget(self._build_venues())
        groups.addWidget(self._build_dates())
        groups.addStretch(1)
        layout.addWidget(self.scroll,1)
        layout.addWidget(self._build_actions())
        self.area_combo.currentIndexChanged.connect(self._refresh_suggestions)
        self.mode_combo.currentIndexChanged.connect(self._refresh_query_options)
        self._refresh_suggestions()

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName('AppSubtitle')
        label.setWordWrap(True)
        return label

    def _build_mode_group(self):
        box = QGroupBox('检索条件')
        layout = QVBoxLayout(box)
        layout.setSpacing(5)
        self.area_combo = QComboBox()
        self.area_combo.addItem('全部学科 · 常用 / 热门建议','all')
        for area in load_catalog().get('areas',[]):
            self.area_combo.addItem(area['label'],area['id'])
        self.area_combo.setToolTip('只切换建议列表，不会自动限制检索结果。')
        layout.addWidget(self.area_combo)
        self.mode_combo = QComboBox()
        for mode,label in MODE_OPTIONS:
            self.mode_combo.addItem(label,mode.value)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_combo,1)
        layout.addLayout(mode_row)
        self.query_combo = SuggestionCombo()
        self.query_edit = self.query_combo.lineEdit()
        self.query_edit.setPlaceholderText('输入片段或选择关键词 / 方向')
        self.query_edit.returnPressed.connect(self._emit_search)
        layout.addWidget(self.query_combo)
        self.match_combo = QComboBox()
        self.match_combo.addItem('模糊匹配 · 词项 / 片段 / 近似','fuzzy')
        self.match_combo.addItem('精确匹配 · 连续词组','exact')
        mode_row.addWidget(self.match_combo,2)
        self.sort_combo = QComboBox()
        for label,key in [('按本次查询匹配','query'),('按我的研究方向','profile'),('按发表日期','recent')]:
            self.sort_combo.addItem(label,key)
        row = QHBoxLayout()
        row.addWidget(self.sort_combo,2)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(5,100)
        self.limit_spin.setValue(int(self.settings.search_cfg.get('default_limit',30)))
        self.limit_spin.setSuffix(' 篇')
        self.limit_spin.setToolTip('每个来源每组查询最多返回的篇数')
        row.addWidget(self.limit_spin)
        layout.addLayout(row)
        layout.addWidget(self._hint('中英建议可自定义；DOI / arXiv ID 始终精确定位。'))
        return box

    def _build_directions(self):
        box = QGroupBox('研究方向 · 多项任一命中')
        layout = QVBoxLayout(box)
        self.direction_picker = MultiValuePicker('topics','输入 / 选择方向，可自定义')
        layout.addWidget(self.direction_picker)
        self.direction_picker.setToolTip('可只选方向检索，也可与上方关键词组合。')
        return box

    def _build_venues(self):
        box = QGroupBox('期刊 / 会议 · 多项任一命中')
        layout = QVBoxLayout(box)
        self.venue_picker = MultiValuePicker('venues','输入片段找期刊 / 会议')
        layout.addWidget(self.venue_picker)
        self.venue_picker.setToolTip('常用期刊与会议示例，非质量排名；空选表示不限。')
        return box

    def _build_dates(self):
        box = QGroupBox('日期范围')
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.date_check = QCheckBox('限定日期')
        row.addWidget(self.date_check)
        self.date_preset = QComboBox()
        for label,key in [('不限时间','all'),('近 7 天','7d'),('近 30 天','30d'),
                          ('近 3 个月','3m'),('近一年','1y'),('今年','year'),('自定义','custom')]:
            self.date_preset.addItem(label,key)
        row.addWidget(self.date_preset,1)
        layout.addLayout(row)
        self.date_from, self.date_to = QDateEdit(), QDateEdit()
        today = QDate.currentDate()
        date_row = QHBoxLayout()
        for label,edit,value in [('从',self.date_from,today.addYears(-3)),('到',self.date_to,today)]:
            edit.setCalendarPopup(True)
            edit.setDisplayFormat('yyyy-MM-dd')
            edit.setLocale(QLocale(QLocale.Chinese,QLocale.China))
            edit.calendarWidget().setFirstDayOfWeek(Qt.Monday)
            edit.setDateRange(QDate(1500,1,1),QDate(today.year()+2,12,31))
            edit.setDate(value)
            edit.setEnabled(False)
            date_row.addWidget(QLabel(label))
            edit.setMinimumWidth(110)
            date_row.addWidget(edit,1)
            edit.dateChanged.connect(self._date_edited)
        layout.addLayout(date_row)
        self.uncertain_dates = QCheckBox('保留日期不完整的文献，标记待核对')
        self.uncertain_dates.setEnabled(False)
        layout.addWidget(self.uncertain_dates)
        layout.addWidget(self._hint('含首尾日期；arXiv 使用首投稿日期。仅年份 / 月份会保留原精度。'))
        self.date_check.toggled.connect(self._date_toggled)
        self.date_preset.currentIndexChanged.connect(self._set_date_preset)
        return box

    def _date_toggled(self, checked):
        for widget in (self.date_from,self.date_to,self.uncertain_dates):
            widget.setEnabled(checked)
        key = self.date_preset.currentData()
        if checked and key == 'all':
            self.date_preset.setCurrentIndex(self.date_preset.findData('custom'))
        elif not checked and key != 'all':
            self.date_preset.setCurrentIndex(0)

    def _date_edited(self, _date):
        previous = self.date_preset.blockSignals(True)
        self.date_preset.setCurrentIndex(self.date_preset.findData('custom'))
        self.date_preset.blockSignals(previous)

    def _set_date_preset(self, _index):
        key = self.date_preset.currentData()
        self.date_check.setChecked(key != 'all')
        if key in ('all','custom'):
            return
        today = QDate.currentDate()
        lower = {'7d':today.addDays(-6),'30d':today.addDays(-29),
                 '3m':today.addMonths(-3),'1y':today.addYears(-1),'year':QDate(today.year(),1,1)}[key]
        for widget,value in ((self.date_from,lower),(self.date_to,today)):
            previous = widget.blockSignals(True)
            widget.setDate(value)
            widget.blockSignals(previous)

    def _refresh_query_options(self, *_args):
        mode = self.mode_combo.currentData()
        options = suggestions('topics',self.area_combo.currentData(),self.settings.profile_terms()) if mode in ('keywords','topic') else []
        self.query_combo.set_options(options)
        self.match_combo.setEnabled(mode not in ('doi','arxiv_id'))

    def _refresh_suggestions(self, *_args):
        self._refresh_query_options()
        area = self.area_combo.currentData()
        self.direction_picker.set_options(suggestions('topics',area,self.settings.directions))
        self.venue_picker.set_options(suggestions('venues',area,self.settings.venues))

    def _build_actions(self):
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0,4,6,0)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet('color: #ffb4b4;')
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.search_button = QPushButton('搜索论文')
        self.search_button.setObjectName('primary')
        self.search_button.setMinimumHeight(36)
        self.search_button.clicked.connect(self._emit_search)
        layout.addWidget(self.search_button)
        row = QHBoxLayout()
        self.profile_button = QPushButton('我的方向')
        self.profile_button.clicked.connect(self.profile_requested.emit)
        row.addWidget(self.profile_button)
        reset = QPushButton('重置条件')
        reset.clicked.connect(self.reset)
        row.addWidget(reset)
        layout.addLayout(row)
        return wrapper

    def current_query(self):
        lower = self.date_from.date().toString('yyyy-MM-dd') if self.date_check.isChecked() else None
        upper = self.date_to.date().toString('yyyy-MM-dd') if self.date_check.isChecked() else None
        if lower and upper and lower > upper:
            lower,upper = upper,lower
        return {'text':self.query_combo.value(),'input_text':self.query_edit.text().strip(),'mode':self.mode_combo.currentData(),
            'date_from':lower,'date_to':upper,'year_from':None,'year_to':None,
            'include_uncertain_dates':self.uncertain_dates.isChecked() and self.date_check.isChecked(),
            'venues':self.venue_picker.values(),'directions':self.direction_picker.values(),
            'limit':self.limit_spin.value(),'sort_by':self.sort_combo.currentData(),
            'match_mode':self.match_combo.currentData()}

    def _emit_search(self):
        payload = self.current_query()
        message = ''
        if not payload['text'] and not payload['directions'] and not payload['venues']:
            message = '请输入检索词，或选择研究方向 / 期刊。'
        elif len(payload['venues']) > 5 or len(payload['directions']) > 5:
            message = '期刊和方向分别最多选择 5 项。'
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))
        if not message:
            self.search_requested.emit(payload)

    def set_busy(self, busy):
        self.search_button.setEnabled(not busy)
        self.search_button.setText('搜索中…' if busy else '搜索论文')

    def set_profile_name(self, name):
        self._profile_label.setText('我的项目：' + (name or '未设置'))
        self._refresh_suggestions()

    def reset(self):
        self.query_combo.setCurrentIndex(-1)
        self.query_edit.clear()
        self.mode_combo.setCurrentIndex(0)
        self.area_combo.setCurrentIndex(0)
        self.match_combo.setCurrentIndex(0)
        self.sort_combo.setCurrentIndex(0)
        self.date_check.setChecked(False)
        self.uncertain_dates.setChecked(False)
        self.direction_picker.clear()
        self.venue_picker.clear()
        self.error_label.hide()
        self.limit_spin.setValue(int(self.settings.search_cfg.get('default_limit',30)))
        self.reset_requested.emit()
