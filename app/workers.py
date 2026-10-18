"""Background task plumbing (QThreadPool + QRunnable).

Every network call, PDF parse and database batch runs here — the GUI thread
never blocks.  Signals are used to hand results back, so widget updates always
happen on the main thread.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str, str)          # message, traceback
    progress = Signal(str, int, int)   # title, downloaded bytes, total bytes
    status = Signal(str)               # human-readable status line


class FunctionWorker(QRunnable):
    """Runs ``fn(*args, **kwargs)`` on a pool thread with signal callbacks."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        status_cb: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._status_cb = status_cb
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            log.exception("background task failed")
            try:
                self.signals.failed.emit(str(exc), traceback.format_exc())
            except RuntimeError:
                pass
        else:
            try:
                self.signals.finished.emit(result)
            except RuntimeError:
                # The window was closed while the task was running.
                pass


class TaskRunner(QObject):
    """Small helper that tracks running tasks and exposes a global pool."""

    busy_changed = Signal(int)

    def __init__(self, parent: Optional[QObject] = None, max_threads: int = 6) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_threads)
        self._active: set[FunctionWorker] = set()

    @property
    def active_count(self) -> int:
        return len(self._active)

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str, str], None]] = None,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
        **kwargs: Any,
    ) -> FunctionWorker:
        worker = FunctionWorker(fn, *args, **kwargs)

        def _done(result: Any) -> None:
            self._forget(worker)
            if on_done is not None:
                on_done(result)

        def _failed(message: str, tb: str) -> None:
            self._forget(worker)
            if on_error is not None:
                on_error(message, tb)

        worker.signals.finished.connect(_done)
        worker.signals.failed.connect(_failed)
        if on_progress is not None:
            worker.signals.progress.connect(on_progress)

        self._active.add(worker)
        self.busy_changed.emit(len(self._active))
        self.pool.start(worker)
        return worker

    def _forget(self, worker: FunctionWorker) -> None:
        self._active.discard(worker)
        self.busy_changed.emit(len(self._active))

    def wait_for_done(self, timeout_ms: int = 5000) -> bool:
        return self.pool.waitForDone(timeout_ms)

    def shutdown(self) -> None:
        try:
            self.pool.clear()
        except RuntimeError:
            pass
        self.pool.waitForDone(3000)
