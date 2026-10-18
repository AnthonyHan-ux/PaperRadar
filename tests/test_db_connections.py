"""SQLite connection lifecycle tests.

Worker threads must not keep the database file open after their task finishes:
on Windows an open handle blocks rename/backup/migration.
"""

from __future__ import annotations

import threading

import pytest

from database.db import Database
from models.paper import Paper


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "paperradar.db")
    yield database
    database.close_all()


def test_close_all_releases_file_for_rename(tmp_path, db):
    db.insert(Paper(title="Lifecycle Paper", doi="10.1000/life"))
    assert db.count() == 1
    db.close_all()

    source = tmp_path / "paperradar.db"
    target = tmp_path / "renamed.db"
    source.rename(target)
    assert target.exists()


def test_worker_thread_connection_is_tracked_and_closed(tmp_path, db):
    opened = threading.Event()

    def worker() -> None:
        db.insert(Paper(title="Worker Paper", doi="10.1000/worker"))
        opened.set()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5.0)
    assert opened.is_set()
    assert db.count() == 1

    db.close_all()
    (tmp_path / "paperradar.db").rename(tmp_path / "after_worker.db")


def test_close_thread_does_not_break_other_threads(db):
    counts: dict[str, int] = {}
    errors: list[Exception] = []

    def worker(name: str) -> None:
        try:
            counts[name] = db.count()
            db.close_thread()
            # Re-open after close: must still work.
            counts[name + "-reopen"] = db.count()
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert not errors
    assert all(value == 0 for value in counts.values())
    # Main-thread connection still usable.
    assert db.count() == 0


def test_close_thread_is_idempotent(db):
    assert db.count() == 0
    db.close_thread()
    db.close_thread()
    assert db.count() == 0
