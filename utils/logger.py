"""Logging setup shared by the GUI and the CLI.

The GUI must show the same log stream that lands in ``logs/app.log``.  A
:class:`QueueLogHandler` buffers records in a bounded deque; a ``QTimer`` in the
log panel drains it on the GUI thread (logging happens on worker threads, and
Qt widgets are not thread-safe).
"""

from __future__ import annotations

import collections
import logging
import logging.handlers
import sys
import threading
from pathlib import Path
from typing import Deque, Iterable, Optional

LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class QueueLogHandler(logging.Handler):
    """Thread-safe ring buffer of formatted log lines for the GUI."""

    def __init__(self, capacity: int = 4000) -> None:
        super().__init__()
        self._buffer: Deque[tuple[str, str]] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - never break logging
            return
        with self._lock:
            self._buffer.append((record.levelname, message))

    def drain(self) -> list[tuple[str, str]]:
        with self._lock:
            items = list(self._buffer)
            self._buffer.clear()
        return items


def setup_logging(
    log_dir: Optional[Path] = None,
    level: str = "INFO",
    console: bool = True,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> QueueLogHandler:
    """Configure the root logger once; return the GUI queue handler."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    queue_handler = QueueLogHandler()
    queue_handler.setFormatter(formatter)
    root.addHandler(queue_handler)

    if log_dir is not None:
        try:
            log_dir = Path(log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_dir / "app.log",
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as exc:  # pragma: no cover - read-only dir
            logging.getLogger(__name__).warning("file logging disabled: %s", exc)

    if console and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    # httpx logs every request at INFO, which drowns our own messages.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return queue_handler


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_records_text(records: Iterable[tuple[str, str]]) -> str:
    return "\n".join(message for _level, message in records)
