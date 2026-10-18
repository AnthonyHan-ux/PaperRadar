"""Headless GUI smoke test.

Renders the main window offscreen, optionally runs a real search, and writes a
PNG screenshot so the layout can be inspected without a desktop session.

    python tools/gui_smoke.py --live --out artifacts/gui.png
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The offscreen platform plugin does not discover system fonts by itself.
os.environ.setdefault("QT_QPA_FONTDIR", str(Path(r"C:\Windows\Fonts")))
# Never touch the real user data directory from a smoke test.
os.environ.setdefault(
    "PAPERRADAR_HOME", str(Path(__file__).resolve().parents[1] / ".smoke-home")
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.main_window import MainWindow  # noqa: E402
from app.theme import apply_theme  # noqa: E402
from models.paper import OaStatus, Paper, PdfStatus  # noqa: E402
from services.app_service import AppService  # noqa: E402


def _fake_papers() -> list[Paper]:
    return [
        Paper(
            title="Beyond Attention: Convolutional Global Context for Remote Sensing Change Detection",
            authors=["Wei Zhang", "Li Wang"],
            year=2026,
            venue="ISPRS Journal of Photogrammetry and Remote Sensing",
            doi="10.1016/j.isprsjprs.2026.01.001",
            abstract="Optical SAR change detection under fog and cloud degradation.",
            oa_status=OaStatus.OPEN_ACCESS,
            pdf_status=PdfStatus.VALIDATED,
            relevance_score=92,
            relevance_band="高度相关",
            relevance_details=[
                {"field": "title", "label": "标题命中", "terms": ["change detection"], "points": 40}
            ],
            local_pdf_path="C:/library/2026/ISPRS_JPRS/demo.pdf",
            sources=["openalex", "arxiv"],
        ),
        Paper(
            title="Reliability-Aware Missing-Modality SAR-Optical Change Detection",
            authors=["Ming Chen"],
            year=2026,
            venue="IEEE TGRS",
            doi="10.1109/TGRS.2026.000123",
            abstract="Missing optical branch, robust fusion, disaster damage mapping.",
            relevance_score=84,
            relevance_band="较相关",
            sources=["crossref"],
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="run a real network search")
    parser.add_argument("--out", default="artifacts/gui_smoke.png")
    parser.add_argument("--query", default="optical SAR change detection")
    parser.add_argument("--tab", type=int, default=0, help="tab index to capture")
    args = parser.parse_args()

    app = QApplication([])
    apply_theme(app)
    service = AppService()
    window = MainWindow(service)
    window.resize(1520, 940)
    window.show()
    if 0 <= args.tab < window.tabs.count():
        window.tabs.setCurrentIndex(args.tab)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    exit_code = {"value": 0}

    def capture_and_quit() -> None:
        window.grab().save(str(out_path))
        print(f"screenshot → {out_path}")
        print(f"papers in table: {len(window.result_table.papers())}")
        print(f"library rows: {window.library_panel.model.rowCount()}")
        exit_code["value"] = 0
        window.close()
        app.quit()

    if args.live:
        window._on_search({
            "text": args.query,
            "mode": "keywords",
            "year_from": 2023,
            "year_to": 2026,
            "venues": [],
            "directions": [],
            "limit": 10,
        })

        def poll() -> None:
            if window.runner.active_count == 0 and not window._search_busy:
                QTimer.singleShot(700, capture_and_quit)
                return
            QTimer.singleShot(500, poll)

        QTimer.singleShot(1000, poll)
        QTimer.singleShot(120_000, capture_and_quit)
    else:
        window.result_table.set_papers(_fake_papers())
        QTimer.singleShot(600, capture_and_quit)

    code = app.exec()
    service.close()
    return code or exit_code["value"]


if __name__ == "__main__":
    sys.exit(main())
