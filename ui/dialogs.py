"""Dialogs: research-profile editor and paper details."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from models.paper import Paper
from utils.config import Settings
from utils.title_normalizer import snippet
from utils.search_catalog import load_catalog, suggestions
from ui.suggestion_picker import SuggestionCombo


def _lines_to_list(text: str) -> list[str]:
    items: list[str] = []
    for raw in text.splitlines():
        for part in raw.split(","):
            value = part.strip()
            if value:
                items.append(value)
    return items


class ProfileDialog(QDialog):
    """Edit ``research_profile.yaml`` without touching the file by hand."""

    def __init__(self, settings: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("我的方向 · 研究画像")
        self.resize(740, 820)
        profile = settings.profile

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.name_edit = QLineEdit(str(profile.get("name") or ""))
        form.addRow("方向名称", self.name_edit)

        self.keywords = self._text_area(profile.get("keywords"), height=72)
        form.addRow("核心关键词", self.keywords)
        self.suggestion_area = QComboBox()
        self.suggestion_area.addItem('全部学科常用建议','all')
        for area in load_catalog().get('areas',[]):
            self.suggestion_area.addItem(area['label'],area['id'])
        form.addRow('推荐学科',self.suggestion_area)
        self.keyword_suggestion = SuggestionCombo()
        form.addRow('关键词选项',self._suggestion_row(self.keyword_suggestion,self.keywords))

        self.positive = self._text_area(profile.get("positive_terms"), height=72)
        form.addRow("加分词", self.positive)

        self.negative = self._text_area(profile.get("negative_terms"), height=72)
        form.addRow("排除词", self.negative)

        self.venues = self._text_area(profile.get("priority_venues"), height=72)
        form.addRow("优先期刊/会议", self.venues)
        self.venue_suggestion = SuggestionCombo()
        form.addRow('期刊选项',self._suggestion_row(self.venue_suggestion,self.venues))
        self.suggestion_area.currentIndexChanged.connect(self._refresh_options)
        self._refresh_options()

        layout.addLayout(form)

        hint = QLabel(
            "每行一个词条（也支持逗号分隔）。\n"
            "标题命中权重最高，其次是摘要、关键词、研究方向与期刊；排除词会扣分。"
        )
        hint.setObjectName("AppSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_options(self, *_args):
        area=self.suggestion_area.currentData()
        self.keyword_suggestion.set_options(suggestions('topics',area))
        self.venue_suggestion.set_options(suggestions('venues',area))

    @staticmethod
    def _suggestion_row(combo, target):
        row=QHBoxLayout()
        row.addWidget(combo,1)
        add=QPushButton('加入')
        def append():
            value=combo.value()
            values=_lines_to_list(target.toPlainText())
            if value and value not in values:
                target.setPlainText('\n'.join([*values,value]))
        add.clicked.connect(append)
        row.addWidget(add)
        return row

    @staticmethod
    def _text_area(values, height: int = 130) -> QPlainTextEdit:
        edit = QPlainTextEdit()
        edit.setPlainText("\n".join(str(v) for v in (values or [])))
        edit.setMinimumHeight(height)
        return edit

    def profile_data(self) -> dict:
        return {
            "name": self.name_edit.text().strip() or "我的方向",
            "keywords": _lines_to_list(self.keywords.toPlainText()),
            "positive_terms": _lines_to_list(self.positive.toPlainText()),
            "negative_terms": _lines_to_list(self.negative.toPlainText()),
            "priority_venues": _lines_to_list(self.venues.toPlainText()),
        }


class DetailsDialog(QDialog):
    def __init__(self, paper: Paper, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("论文详情")
        self.resize(760, 620)

        layout = QVBoxLayout(self)
        title = QLabel(paper.title)
        title.setWordWrap(True)
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        meta = [
            f"作者：{', '.join(paper.authors) or '—'}",
            f"年份：{paper.year or '—'}    期刊/会议：{paper.venue or '—'}",
            paper.date_label,
            f"DOI：{paper.doi or '—'}    arXiv：{paper.arxiv_id or '—'}",
            f"OA：{paper.oa_status.value}    PDF：{paper.pdf_status.value}",
            f"相关性：{paper.relevance_score if paper.relevance_score is not None else '—'} "
            f"（{paper.relevance_band or '未评分'}）",
        ]
        meta_label = QLabel("\n".join(meta))
        meta_label.setObjectName("AppSubtitle")
        meta_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(meta_label)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        html_parts = [f"<h3>摘要</h3><p>{snippet(paper.abstract, 4000) or '（无摘要）'}</p>"]
        if paper.relevance_details:
            html_parts.append("<h3>相关性打分依据</h3><ul>")
            for item in paper.relevance_details:
                terms = "、".join(item.get("terms", [])[:8])
                html_parts.append(
                    f"<li>{item.get('label', '')}：{terms} "
                    f"→ {item.get('points', 0):+.0f}</li>"
                )
            html_parts.append("</ul>")
        if paper.pdf_urls:
            html_parts.append("<h3>公开 PDF 候选</h3><ul>")
            for url in paper.pdf_urls[:10]:
                html_parts.append(f'<li><a href="{url}">{url}</a></li>')
            html_parts.append("</ul>")
        if paper.source_url:
            html_parts.append(
                f'<h3>来源页</h3><p><a href="{paper.source_url}">{paper.source_url}</a></p>'
            )
        if paper.local_pdf_path:
            html_parts.append(f"<h3>本地文件</h3><p>{paper.local_pdf_path}</p>")
        if paper.sources:
            html_parts.append("<h3>元数据来源</h3><p>" + ", ".join(paper.sources) + "</p>")
        browser.setHtml("".join(html_parts))
        layout.addWidget(browser, 1)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)
