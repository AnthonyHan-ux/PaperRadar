"""PaperRadar entry point.

    PaperRadar.exe                     # GUI
    PaperRadar.exe --cli search -q …   # headless CLI
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


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


def _run_gui(home: str | None = None, log_level: str | None = None,
             smoke_path: str | None = None) -> int:
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

    parser = argparse.ArgumentParser(
        prog="PaperRadar",
        description="遥感文献雷达（不带参数启动图形界面；加 --cli 使用命令行）",
    )
    parser.add_argument("--home", help="数据目录")
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING")
    parser.add_argument("--smoke", metavar="PNG", default=None,
                        help="启动后截图并退出（用于打包自检）")
    parser.add_argument("--cli", action="store_true", help=argparse.SUPPRESS)
    args, _unknown = parser.parse_known_args(argv)
    return _run_gui(args.home, args.log_level, args.smoke)


if __name__ == "__main__":
    sys.exit(main())
