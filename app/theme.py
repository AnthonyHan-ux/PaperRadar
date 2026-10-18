"""Qt styling — a compact dark theme that does not look like WinForms."""

from __future__ import annotations

DARK_QSS = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", "Noto Sans SC", sans-serif;
    font-size: 13px;
}
QMainWindow, QDialog, QWidget {
    background-color: #15181d;
    color: #d6dbe4;
}
QMenuBar, QMenu {
    background-color: #1b1f26;
    color: #d6dbe4;
    border: 1px solid #2c323d;
}
QMenu::item:selected, QMenuBar::item:selected { background-color: #2a3140; }

QFrame#Header {
    background-color: #1b1f26;
    border-bottom: 1px solid #2c323d;
}
QLabel#AppTitle {
    font-size: 20px;
    font-weight: 700;
    color: #e8eef8;
}
QLabel#AppSubtitle { color: #7f8a9c; }
QLabel#Counter { color: #7f8a9c; }
QLabel#CounterValue { color: #79c0ff; font-weight: 600; }

QGroupBox {
    border: 1px solid #2c323d;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    background-color: #191d24;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #8fa0b8;
}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background-color: #101318;
    border: 1px solid #2f3641;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #2f6feb;
    color: #dfe6f1;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #4b8bf5;
}
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background-color: #1b1f26;
    border: 1px solid #2f3641;
    selection-background-color: #2f6feb;
    outline: 0;
}

QPushButton {
    background-color: #232933;
    border: 1px solid #333c49;
    border-radius: 6px;
    padding: 6px 12px;
    color: #d6dbe4;
}
QPushButton:hover { background-color: #2b333f; border-color: #445268; }
QPushButton:pressed { background-color: #1c222b; }
QPushButton:disabled { color: #5d6776; background-color: #1c2028; border-color: #262c36; }
QPushButton#primary {
    background-color: #2f6feb;
    border: 1px solid #3b7cf7;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primary:hover { background-color: #3d7dfa; }
QPushButton#primary:disabled { background-color: #26364d; color: #6f7f96; }
QPushButton#danger { color: #ffb4b4; }

QTableView {
    background-color: #121519;
    alternate-background-color: #161a20;
    gridline-color: #232932;
    border: 1px solid #262c36;
    border-radius: 6px;
    selection-background-color: #24406b;
    selection-color: #ffffff;
}
QTableView::item { padding: 3px 4px; }
QHeaderView::section {
    background-color: #1b1f26;
    color: #93a1b5;
    padding: 6px 6px;
    border: none;
    border-right: 1px solid #262c36;
    border-bottom: 1px solid #2c323d;
    font-weight: 600;
}
QTableCornerButton::section { background-color: #1b1f26; border: none; }

QTabWidget::pane { border: 1px solid #262c36; border-radius: 6px; top: -1px; }
QTabBar::tab {
    background: #1b1f26;
    color: #93a1b5;
    padding: 7px 14px;
    border: 1px solid #262c36;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #232a35; color: #e8eef8; }
QTabBar::tab:hover { color: #cfd8e6; }

QListWidget, QListView {
    background-color: #101318;
    border: 1px solid #2c323d;
    border-radius: 6px;
    outline: 0;
}
QListWidget::item { padding: 3px 4px; }
QListWidget::item:selected { background-color: #24406b; color: #ffffff; }

QProgressBar {
    background-color: #101318;
    border: 1px solid #2c323d;
    border-radius: 6px;
    text-align: center;
    color: #cbd5e3;
    height: 16px;
}
QProgressBar::chunk { background-color: #2f6feb; border-radius: 5px; }

QScrollBar:vertical {
    background: #15181d; width: 10px; margin: 0;
}
QScrollBar::handle:vertical {
    background: #333c49; border-radius: 5px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #445268; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #15181d; height: 10px; margin: 0; }
QScrollBar::handle:horizontal {
    background: #333c49; border-radius: 5px; min-width: 24px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QSplitter::handle { background-color: #262c36; }
QSplitter::handle:horizontal { width: 3px; }
QSplitter::handle:vertical { height: 3px; }

QStatusBar { background: #1b1f26; color: #8c98aa; border-top: 1px solid #262c36; }
QToolTip {
    background-color: #232a35;
    color: #dfe6f1;
    border: 1px solid #3a4455;
    padding: 6px;
}
QLabel#SectionTitle { color: #8fa0b8; font-weight: 600; }
QCheckBox { color: #c3ccda; spacing: 6px; }
QCheckBox::indicator {
    width: 14px; height: 14px; border-radius: 3px;
    border: 1px solid #3a4455; background: #101318;
}
QCheckBox::indicator:checked { background: #2f6feb; border-color: #4b8bf5; }
"""


def apply_theme(app) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)


def make_app_icon(size: int = 64):
    """Paint a small radar icon so the window/taskbar is not the Qt default."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#2f6feb"))
    painter.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), size * 0.22, size * 0.22)

    pen = QPen(QColor("#e8eef8"))
    pen.setWidth(max(2, size // 22))
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    inset = size * 0.25
    painter.drawEllipse(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    painter.drawEllipse(QRectF(size * 0.42, size * 0.42, size * 0.16, size * 0.16))
    painter.drawLine(int(size / 2), int(size * 0.10), int(size / 2), int(size * 0.90))
    painter.drawLine(int(size * 0.10), int(size / 2), int(size * 0.90), int(size / 2))
    painter.end()
    return QIcon(pixmap)
