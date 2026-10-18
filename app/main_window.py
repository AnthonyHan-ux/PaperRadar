"""Main window: header, search panel, result tabs and action bar."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.theme import apply_theme, make_app_icon
from app.workers import TaskRunner
from models.paper import PdfStatus, Paper, SearchMode
from providers.base import SearchQuery
from services.app_service import AppService
from services.download_service import DownloadResult
from ui.dialogs import DetailsDialog, ProfileDialog
from ui.download_panel import DownloadPanel
from ui.library_panel import LibraryPanel
from ui.log_panel import LogPanel
from ui.result_table import ResultTable
from ui.search_panel import SearchPanel
from utils.config import APP_NAME, APP_VERSION

log = logging.getLogger(__name__)


class DownloadBridge(QObject):
    """Marshals worker-thread download callbacks onto the GUI thread."""

    result_ready = Signal(object)
    progress_ready = Signal(str, int, int)


class MainWindow(QMainWindow):
    def __init__(self, service: AppService) -> None:
        super().__init__()
        self.service = service
        self.settings = service.settings
        self.runner = TaskRunner(self, max_threads=6)
        self._cancel_event = threading.Event()
        self._papers: list[Paper] = []
        self._download_busy = False
        self._search_busy = False

        self.setWindowTitle(f"{APP_NAME} · 遥感文献雷达  v{APP_VERSION}")
        self.resize(1520, 940)
        self.setMinimumSize(1180, 720)
        try:
            self.setWindowIcon(make_app_icon())
        except Exception:  # pragma: no cover - icon is cosmetic
            pass

        self._build_ui()
        self._build_menus()
        self._update_counters()
        self._refresh_library()
        self.log_panel.append_line(
            f"PaperRadar 已启动 | 数据目录：{self.settings.paths.home}", "#79c0ff"
        )
        self.log_panel.append_line(
            "当前方向：" + str(self.settings.profile.get("name") or "未设置"), "#8b949e"
        )
        if not self.settings.mailto:
            self.log_panel.append_line(
                "提示：在 config/config.yaml 填写 api.openalex_mailto / unpaywall_email "
                "可提升限流额度并启用 Unpaywall。", "#ffd166",
            )

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(12, 12, 12, 8)
        body_layout.setSpacing(10)

        self.search_panel = SearchPanel(self.settings)
        self.search_panel.setMinimumWidth(300)
        self.search_panel.setMaximumWidth(380)
        self.search_panel.search_requested.connect(self._on_search)
        self.search_panel.profile_requested.connect(self._open_profile)
        self.search_panel.reset_requested.connect(lambda: self.statusBar().showMessage("条件已重置"))

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.search_panel)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.tabs = QTabWidget()
        self.result_table = ResultTable()
        self.result_table.selection_changed.connect(self._on_selection_changed)
        self.result_table.open_source_requested.connect(self._open_source)
        self.result_table.open_pdf_requested.connect(self._open_pdf)
        self.result_table.download_requested.connect(self._start_download)
        self.result_table.details_requested.connect(self._show_details)
        self.tabs.addTab(self.result_table, "搜索结果")

        self.library_panel = LibraryPanel()
        self.library_panel.open_pdf_requested.connect(self._open_pdf)
        self.library_panel.open_folder_requested.connect(self._open_folder)
        self.library_panel.refresh_requested.connect(self._refresh_library)
        self.tabs.addTab(self.library_panel, "本地文献库")

        self.download_panel = DownloadPanel()
        self.download_panel.cancel_requested.connect(self._cancel_download)
        self.tabs.addTab(self.download_panel, "下载队列")

        self.log_panel = LogPanel(self.service.log_handler)
        self.log_panel.set_log_dir(self.settings.paths.logs_dir)
        self.tabs.addTab(self.log_panel, "日志")

        right_layout.addWidget(self.tabs, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([330, 1100])
        body_layout.addWidget(splitter, 1)
        root.addWidget(body, 1)
        root.addWidget(self._build_action_bar())

        self.statusBar().showMessage("就绪")
        self._busy_label = QLabel("")
        self.statusBar().addPermanentWidget(self._busy_label)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 10, 16, 10)

        title_box = QVBoxLayout()
        title = QLabel("PaperRadar")
        title.setObjectName("AppTitle")
        subtitle = QLabel("遥感文献雷达 · 检索 / 去重 / 开放获取 PDF 自动下载")
        subtitle.setObjectName("AppSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addStretch(1)

        self.count_value = QLabel("0")
        self.count_value.setObjectName("CounterValue")
        self.pdf_value = QLabel("0")
        self.pdf_value.setObjectName("CounterValue")
        for label, value in (("本地文献", self.count_value), ("已有 PDF", self.pdf_value)):
            box = QVBoxLayout()
            caption = QLabel(label)
            caption.setObjectName("Counter")
            box.addWidget(value)
            box.addWidget(caption)
            layout.addLayout(box)
            layout.addSpacing(18)
        return header

    def _build_action_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Header")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        self.btn_download_sel = QPushButton("下载选中公开 PDF")
        self.btn_download_sel.clicked.connect(self._download_selected)
        layout.addWidget(self.btn_download_sel)

        self.btn_download_all = QPushButton("下载全部公开 PDF")
        self.btn_download_all.clicked.connect(self._download_all)
        layout.addWidget(self.btn_download_all)

        self.btn_add = QPushButton("加入本地文献库")
        self.btn_add.clicked.connect(self._add_selected_to_library)
        layout.addWidget(self.btn_add)

        layout.addSpacing(12)
        self.btn_csv = QPushButton("导出 CSV")
        self.btn_csv.clicked.connect(lambda: self._export("csv"))
        layout.addWidget(self.btn_csv)

        self.btn_bib = QPushButton("导出 BibTeX")
        self.btn_bib.clicked.connect(lambda: self._export("bib"))
        layout.addWidget(self.btn_bib)

        self.btn_ris = QPushButton("导出 RIS")
        self.btn_ris.clicked.connect(lambda: self._export("ris"))
        layout.addWidget(self.btn_ris)

        layout.addStretch(1)

        self.btn_folder = QPushButton("打开论文文件夹")
        self.btn_folder.clicked.connect(self._open_selected_folder)
        layout.addWidget(self.btn_folder)

        self.btn_source = QPushButton("打开论文来源页")
        self.btn_source.clicked.connect(self._open_selected_source)
        layout.addWidget(self.btn_source)
        return bar

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件")
        export_csv = QAction("导出 CSV…", self)
        export_csv.triggered.connect(lambda: self._export("csv"))
        file_menu.addAction(export_csv)
        export_bib = QAction("导出 BibTeX…", self)
        export_bib.triggered.connect(lambda: self._export("bib"))
        file_menu.addAction(export_bib)
        export_ris = QAction("导出 RIS…", self)
        export_ris.triggered.connect(lambda: self._export("ris"))
        file_menu.addAction(export_ris)
        file_menu.addSeparator()
        open_data = QAction("打开数据目录", self)
        open_data.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.settings.paths.home)))
        )
        file_menu.addAction(open_data)
        open_library = QAction("打开文献库目录", self)
        open_library.triggered.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.settings.paths.library_dir))
            )
        )
        file_menu.addAction(open_library)
        file_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        search_menu = menubar.addMenu("检索")
        profile_action = QAction("我的方向…", self)
        profile_action.setShortcut(QKeySequence("Ctrl+P"))
        profile_action.triggered.connect(self._open_profile)
        search_menu.addAction(profile_action)
        focus_action = QAction("聚焦搜索框", self)
        focus_action.setShortcut(QKeySequence.Find)
        focus_action.triggered.connect(lambda: self.search_panel.query_edit.setFocus())
        search_menu.addAction(focus_action)
        refresh_action = QAction("刷新本地文献库", self)
        refresh_action.setShortcut(QKeySequence.Refresh)
        refresh_action.triggered.connect(lambda: self._refresh_library())
        search_menu.addAction(refresh_action)

        help_menu = menubar.addMenu("帮助")
        about = QAction("关于 PaperRadar", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    # ------------------------------------------------------------------ #
    # Counters / status
    # ------------------------------------------------------------------ #
    def _update_counters(self) -> None:
        try:
            stats = self.service.stats()
        except Exception:  # pragma: no cover
            stats = None
        total = stats.total if stats else 0
        with_pdf = stats.with_pdf if stats else 0
        self.count_value.setText(f"{total}")
        self.pdf_value.setText(f"{with_pdf}")
        self.pdf_value.setToolTip("已有本地 PDF 的论文数")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy_label.setText(message)
        self.search_panel.set_busy(busy)
        for button in (self.btn_download_sel, self.btn_download_all):
            button.setEnabled(not busy)
        QApplication.processEvents()

    def _on_selection_changed(self, count: int) -> None:
        self.statusBar().showMessage(f"已选择 {count} 篇" if count else "就绪")

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #
    def _on_search(self, payload: dict) -> None:
        if self._search_busy:
            return
        try:
            mode = SearchMode(payload.get("mode", "keywords"))
        except ValueError:
            mode = SearchMode.KEYWORDS
        query = SearchQuery(
            text=payload.get("text", ""),
            mode=mode,
            year_from=payload.get("year_from"),
            year_to=payload.get("year_to"),
            venues=payload.get("venues") or [],
            limit=int(payload.get("limit") or 30),
        )
        query.extra["directions"] = payload.get("directions") or []

        self._search_busy = True
        self._set_busy(True, "检索中…")
        self.statusBar().showMessage(f"正在检索：{query.text}")
        self.log_panel.append_line(
            f"开始检索 [{mode.value}] {query.text!r} "
            f"年份={payload.get('year_from') or '不限'}-{payload.get('year_to') or '不限'}",
            "#79c0ff",
        )

        def done(outcome) -> None:
            self._search_busy = False
            self._set_busy(False)
            self._papers = outcome.papers
            self.result_table.set_papers(outcome.papers)
            self.tabs.setCurrentWidget(self.result_table)
            self._update_counters()
            self.statusBar().showMessage(outcome.summary_text())
            self.log_panel.append_line(outcome.summary_text(), "#7ee787")
            for name, error in outcome.errors():
                self.log_panel.append_line(f"{name} 检索失败：{error}", "#ff7b72")
            if not outcome.papers:
                self.log_panel.append_line(
                    "没有结果。可尝试更短的关键词、放宽年份，或检查网络。", "#ffd166"
                )

        def failed(message: str, traceback_text: str) -> None:
            self._search_busy = False
            self._set_busy(False)
            self.statusBar().showMessage("检索失败")
            self.log_panel.append_line(f"检索失败：{message}", "#ff7b72")
            log.error("search failed: %s\n%s", message, traceback_text)
            QMessageBox.warning(self, "检索失败", f"{message}\n\n详情见日志。")

        self.runner.submit(self.service.run_search, query, on_done=done, on_error=failed)

    # ------------------------------------------------------------------ #
    # Download
    # ------------------------------------------------------------------ #
    def _download_selected(self) -> None:
        papers = self.result_table.selected_papers()
        if not papers:
            QMessageBox.information(self, "提示", "请先在结果表中选中论文。")
            return
        self._start_download(papers)

    def _download_all(self) -> None:
        papers = self.result_table.papers()
        if not papers:
            QMessageBox.information(self, "提示", "当前没有检索结果。")
            return
        answer = QMessageBox.question(
            self,
            "下载全部公开 PDF",
            f"将尝试为 {len(papers)} 篇论文查找并下载合法公开 PDF。\n"
            "没有公开副本的论文会被标注原因，不会尝试绕过付费墙。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._start_download(papers)

    def _start_download(self, papers: list[Paper]) -> None:
        if self._download_busy:
            QMessageBox.information(self, "提示", "已有下载任务在进行中。")
            return
        papers = [p for p in papers if p is not None]
        if not papers:
            return
        self._download_busy = True
        self._cancel_event.clear()
        self.download_panel.clear()
        self.download_panel.start(len(papers))
        self.tabs.setCurrentWidget(self.download_panel)
        self.statusBar().showMessage(f"正在下载 {len(papers)} 篇…")
        self.log_panel.append_line(f"开始下载 {len(papers)} 篇论文的公开 PDF", "#79c0ff")

        bridge = DownloadBridge(self)
        bridge.result_ready.connect(self._on_download_result)
        bridge.progress_ready.connect(self.download_panel.update_progress)
        self._download_bridge = bridge  # keep alive

        def job():
            return self.service.download_many(
                papers,
                cancel_event=self._cancel_event,
                progress=bridge.progress_ready.emit,
                on_result=bridge.result_ready.emit,
            )

        def done(results) -> None:
            self._download_busy = False
            self.download_panel.finish()
            self._set_busy(False)
            counts: dict[str, int] = {}
            for result in results or []:
                counts[result.status.value] = counts.get(result.status.value, 0) + 1
            summary = "，".join(f"{k} {v}" for k, v in sorted(counts.items()))
            self.statusBar().showMessage("下载完成：" + (summary or "无结果"))
            self.log_panel.append_line("下载完成：" + (summary or "无结果"), "#7ee787")
            self._refresh_library()
            self._update_counters()

        def failed(message: str, traceback_text: str) -> None:
            self._download_busy = False
            self.download_panel.finish()
            self._set_busy(False)
            self.log_panel.append_line(f"下载任务失败：{message}", "#ff7b72")
            log.error("download failed: %s\n%s", message, traceback_text)
            QMessageBox.warning(self, "下载失败", f"{message}\n\n详情见日志。")

        self.runner.submit(job, on_done=done, on_error=failed)

    def _on_download_result(self, result: DownloadResult) -> None:
        self.download_panel.add_result(result)
        paper = result.paper
        if paper is not None:
            self.result_table.update_paper(paper)
            self.result_table.set_note(paper, result.message)
        self.log_panel.append_line(
            f"[{result.status.value}] {result.title[:70]} — {result.message}",
            "#7ee787" if result.ok else "#ffb86b",
        )

    def _cancel_download(self) -> None:
        if self._download_busy:
            self._cancel_event.set()
            self.log_panel.append_line("已请求取消下载，等待当前文件收尾…", "#ffd166")
            self.statusBar().showMessage("正在取消下载…")

    # ------------------------------------------------------------------ #
    # Library / export
    # ------------------------------------------------------------------ #
    def _refresh_library(self, search: str = "", only_local: bool = False) -> None:
        def job():
            return self.service.library_papers(
                limit=2000, only_local=only_local, search=search
            )

        self.runner.submit(
            job,
            on_done=lambda papers: (self.library_panel.set_papers(papers),
                                    self._update_counters()),
            on_error=lambda message, tb: self.log_panel.append_line(
                f"读取文献库失败：{message}", "#ff7b72"
            ),
        )

    def _add_selected_to_library(self) -> None:
        papers = self.result_table.selected_papers() or self.result_table.papers()
        if not papers:
            QMessageBox.information(self, "提示", "没有可加入的论文。")
            return
        added, existing = self.service.add_many(papers)
        self.statusBar().showMessage(f"已加入 {added} 篇，已存在 {existing} 篇")
        self.log_panel.append_line(f"文献库写入：新增 {added}，合并 {existing}", "#7ee787")
        self._refresh_library()
        self._update_counters()

    def _export(self, fmt: str) -> None:
        papers = self.result_table.selected_papers() or self.result_table.papers()
        if not papers:
            QMessageBox.information(self, "提示", "没有可导出的论文。")
            return
        suffix = {"csv": "csv", "bib": "bib", "ris": "ris"}[fmt]
        default_path = self.settings.paths.export_dir / self.service.exporter.default_filename(fmt)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出文献", str(default_path),
            f"{suffix.upper()} 文件 (*.{suffix})",
        )
        if not path:
            return
        try:
            written = self.service.export_papers(papers, fmt, Path(path))
        except Exception as exc:  # pragma: no cover - filesystem errors
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.statusBar().showMessage(f"已导出 {len(papers)} 篇 → {written}")
        self.log_panel.append_line(f"导出 {len(papers)} 篇 → {written}", "#7ee787")

    # ------------------------------------------------------------------ #
    # Open helpers
    # ------------------------------------------------------------------ #
    def _open_source(self, paper: Paper) -> None:
        url = paper.source_url or paper.url
        if not url:
            QMessageBox.information(self, "提示", "该论文没有来源链接。")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _open_pdf(self, paper: Paper) -> None:
        if paper.local_pdf_path and self.service.library.open_file(paper.local_pdf_path):
            return
        if paper.pdf_urls:
            QDesktopServices.openUrl(QUrl(paper.pdf_urls[0]))
            return
        QMessageBox.information(
            self, "提示", "本地没有 PDF，也没有可用的公开链接。可先执行“下载公开 PDF”。"
        )

    def _open_folder(self, paper: Paper) -> None:
        if paper.local_pdf_path:
            self.service.library.open_in_explorer(paper.local_pdf_path)
        else:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.settings.paths.library_dir))
            )

    def _open_selected_folder(self) -> None:
        papers = self.result_table.selected_papers()
        if papers and papers[0].local_pdf_path:
            self._open_folder(papers[0])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.settings.paths.library_dir)))

    def _open_selected_source(self) -> None:
        papers = self.result_table.selected_papers()
        if not papers:
            QMessageBox.information(self, "提示", "请先选中论文。")
            return
        self._open_source(papers[0])

    def _show_details(self, paper: Paper) -> None:
        dialog = DetailsDialog(paper, self)
        dialog.exec()

    # ------------------------------------------------------------------ #
    def _open_profile(self) -> None:
        dialog = ProfileDialog(self.settings, self)
        if dialog.exec() != dialog.Accepted:
            return
        try:
            self.service.save_profile(dialog.profile_data())
        except Exception as exc:  # pragma: no cover - filesystem
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.settings = self.service.settings
        self.search_panel.set_profile_name(str(self.settings.profile.get("name") or ""))
        self.log_panel.append_line("研究画像已保存，下次检索生效。", "#7ee787")
        self.statusBar().showMessage("研究画像已保存")

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于 PaperRadar",
            f"<b>PaperRadar</b> v{APP_VERSION}<br>"
            "遥感文献雷达：多源检索 · 去重 · 开放获取 PDF 下载 · 本地文献库<br><br>"
            "只访问合法公开来源（arXiv / OpenAlex / Unpaywall / OpenReview 等），"
            "不绕过付费墙、不访问侵权站点。<br><br>"
            f"数据目录：{self.settings.paths.home}",
        )

    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._cancel_event.set()
            self.runner.shutdown()
            self.log_panel.stop()
            self.service.close()
        except Exception:  # pragma: no cover - shutdown best effort
            pass
        super().closeEvent(event)


def run_app(service: Optional[AppService] = None) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    apply_theme(app)
    service = service or AppService()
    window = MainWindow(service)
    window.show()
    return app.exec()
