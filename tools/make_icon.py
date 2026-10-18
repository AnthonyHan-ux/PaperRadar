"""Generate assets/paperradar.ico from the same design as the window icon.

    .\\.venv\\Scripts\\python.exe tools/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def _pixmap(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#2f6feb"))
    radius = size * 0.22
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), radius, radius)

    pen = QPen(QColor("#e8eef8"))
    pen.setWidth(max(1.0, size / 22.0))
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    inset = size * 0.25
    painter.drawEllipse(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    painter.drawEllipse(
        QRectF(size * 0.42, size * 0.42, size * 0.16, size * 0.16)
    )
    painter.drawLine(int(size / 2), int(size * 0.10), int(size / 2), int(size * 0.90))
    painter.drawLine(int(size * 0.10), int(size / 2), int(size * 0.90), int(size / 2))
    painter.end()
    return pixmap


def main() -> int:
    app = QApplication.instance() or QApplication([])  # noqa: F841 - needed for QPixmap
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(_pixmap(size))

    target = ROOT / "assets" / "paperradar.ico"
    target.parent.mkdir(parents=True, exist_ok=True)

    # Prefer a real multi-resolution .ico; fall back to a 256px PNG if the Qt
    # build lacks the ICO writer.
    image = _pixmap(256).toImage()
    if image.save(str(target), "ICO"):
        print(f"wrote {target} ({target.stat().st_size} bytes)")
        return 0

    png = ROOT / "assets" / "paperradar.png"
    _pixmap(256).save(str(png), "PNG")
    print(f"ICO writer unavailable; wrote {png}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
