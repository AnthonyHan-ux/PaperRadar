"""PaperRadar entry point.

    PaperRadar.exe                     # GUI
    PaperRadar.exe --cli search -q …   # headless CLI
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import logging


def _set_windows_app_id() -> None:
    """Give the window its own taskbar identity (nicer icon grouping)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            "PaperRadar.RemoteSensing.LiteratureRadar"
        )
    except Exception:
        pass


def _start_selftest(app, window, out_json, query: str) -> None:
    """Drive the real GUI through search + download and write a JSON report.

    Used by the release checklist to prove that the *packaged* GUI can download
    and persist a PDF, without a human clicking buttons.
    """
    import json
    import time as _time
    from pathlib import Path

    from PySide6.QtCore import QTimer

    report: dict = {"ok": False, "query": query, "papers": 0, "downloaded": []}
    state = {"phase": "search", "deadline": _time.time() + 300}
    logging.getLogger("selftest").info("GUI selftest starting, query=%r", query)

    def write_report() -> None:
        target = Path(out_json)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"selftest report: {target}")
        except OSError:  # pragma: no cover
            pass

    def finish(ok: bool, message: str = "") -> None:
        report["ok"] = ok
        if message:
            report["message"] = message
        logging.getLogger("selftest").info("GUI selftest finished: %s %s", ok, message)
        try:
            window.grab().save(str(Path(out_json).with_suffix(".png")))
        except Exception:  # pragma: no cover - screenshot is optional
            pass
        write_report()
        window.close()
        app.quit()

    def tick() -> None:
        try:
            logging.getLogger("selftest").debug(
                "tick phase=%s busy=%s/%s papers=%d",
                state["phase"], window._search_busy, window._download_busy,
                window.result_table.model.rowCount(),
            )
            _tick()
        except Exception as exc:  # noqa: BLE001 - report instead of hanging
            import traceback

            traceback.print_exc()
            finish(False, f"{type(exc).__name__}: {exc}")

    def _tick() -> None:
        if _time.time() > state["deadline"]:
            finish(False, f"timeout during {state['phase']}")
            return

        if state["phase"] == "search":
            if window._search_busy or window.runner.active_count:
                return
            papers = window.result_table.papers()
            report["papers"] = len(papers)
            if not papers:
                finish(False, "search returned no papers")
                return
            chosen = next(
                (p for p in papers if p.pdf_candidates or p.arxiv_id or p.doi),
                papers[0],
            )
            report["target_title"] = chosen.title
            state["phase"] = "download"
            window._start_download([chosen])
            return

        if window._download_busy or window.runner.active_count:
            return

        papers = window.result_table.papers()
        report["downloaded"] = [
            {
                "title": p.title,
                "status": p.pdf_status.value,
                "path": p.local_pdf_path,
                "exists": bool(p.local_pdf_path and Path(p.local_pdf_path).is_file()),
            }
            for p in papers
            if p.pdf_status.value
            not in ("NOT_CHECKED", "OPEN_ACCESS_FOUND", "SEARCHING", "DOWNLOADING")
        ]
        ok = any(item["status"] in ("VALIDATED", "TITLE_WARNING") for item in report["downloaded"])
        finish(ok, "" if ok else "no validated PDF produced")

    # A single recurring timer is more reliable than a chain of singleShot()
    # calls: the QObject lives on the window, so nothing can be garbage
    # collected while the event loop runs.
    timer = QTimer(window)
    timer.setInterval(500)
    timer.timeout.connect(tick)
    timer.start()
    window._selftest_timer = timer
    tick()


def _run_gui(home: str | None = None, log_level: str | None = None,
             smoke_path: str | None = None, selftest_path: str | None = None,
             selftest_query: str = "optical SAR change detection",
             selftest_doi: str | None = None) -> int:
    _set_windows_app_id()

    from utils.config import load_settings
    from utils.logger import setup_logging

    settings = load_settings(home=Path(home) if home else None)
    if log_level:
        settings.config.setdefault("app", {})["log_level"] = log_level
    setup_logging(settings.paths.logs_dir, level=str(settings.app_cfg.get("log_level", "INFO")))

    # Imports deferred so that `--cli` works even if Qt cannot load.
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.main_window import MainWindow
    from app.theme import apply_theme, make_app_icon
    from services.app_service import AppService
    from utils.config import APP_NAME, APP_VERSION

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("PaperRadar")
    try:
        app.setWindowIcon(make_app_icon())
    except Exception:  # pragma: no cover - icon is cosmetic
        pass

    service = None
    try:
        apply_theme(app)
        service = AppService(settings=settings)
        window = MainWindow(service)
        window.show()
        if smoke_path:
            # Packaging self-check: render the real window and exit.
            def _smoke() -> None:
                window.grab().save(smoke_path)
                print(f"smoke screenshot: {smoke_path}")
                window.close()
                app.quit()

            QTimer.singleShot(1500, _smoke)
        elif selftest_path:
            payload = {
                "text": selftest_doi or selftest_query,
                "mode": "doi" if selftest_doi else "keywords",
                "year_from": None if selftest_doi else 2022,
                "year_to": None if selftest_doi else 2030,
                "venues": [],
                "directions": [],
                "limit": 10,
            }

            def _begin_selftest() -> None:
                window._on_search(payload)
                _start_selftest(app, window, selftest_path, payload["text"])

            QTimer.singleShot(1200, _begin_selftest)
        return app.exec()
    except Exception as exc:  # startup failure: show it instead of dying silently
        traceback.print_exc()
        try:
            QMessageBox.critical(
                None,
                "PaperRadar 启动失败",
                f"{type(exc).__name__}: {exc}\n\n"
                f"日志：{settings.paths.logs_dir / 'app.log'}",
            )
        except Exception:
            pass
        return 1
    finally:
        if service is not None:
            try:
                service.close()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--cli" in argv:
        argv = [item for item in argv if item != "--cli"]
        from cli import main as cli_main

        return cli_main(argv)

    import argparse

    from utils.config import APP_VERSION

    parser = argparse.ArgumentParser(
        prog="PaperRadar",
        description="遥感文献雷达（不带参数启动图形界面；加 --cli 使用命令行）",
    )
    parser.add_argument("--home", help="数据目录")
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING")
    parser.add_argument(
        "--version", action="version", version=f"PaperRadar {APP_VERSION}"
    )
    parser.add_argument("--smoke", metavar="PNG", default=None,
                        help="启动后截图并退出（用于打包自检）")
    parser.add_argument("--selftest", metavar="JSON", default=None,
                        help="自动检索+下载一篇并写 JSON 报告（发布自检）")
    parser.add_argument("--selftest-query", default="optical SAR change detection")
    parser.add_argument("--selftest-doi", default=None,
                        help="自检时直接按 DOI 定位（结果可复现）")
    parser.add_argument("--cli", action="store_true", help=argparse.SUPPRESS)
    args, _unknown = parser.parse_known_args(argv)
    return _run_gui(
        args.home, args.log_level, args.smoke, args.selftest,
        args.selftest_query, args.selftest_doi,
    )


if __name__ == "__main__":
    sys.exit(main())
