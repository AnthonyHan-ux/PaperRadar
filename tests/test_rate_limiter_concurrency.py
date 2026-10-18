"""Rate limiter concurrency tests (no network)."""

from __future__ import annotations

import threading
import time

from utils.rate_limiter import RateLimiter


def test_small_interval_is_enforced():
    limiter = RateLimiter(intervals={"k": 0.05})
    start = time.monotonic()
    limiter.wait("k")
    limiter.wait("k")
    assert time.monotonic() - start >= 0.04


def test_arxiv_sleep_does_not_block_other_keys():
    """The old implementation slept while holding the lock: a 3.2 s arXiv wait
    blocked every other provider.  Slots must be reserved without holding it."""
    limiter = RateLimiter(intervals={"arxiv": 0.4, "openalex": 0.01})
    limiter.wait("arxiv")  # reserve the first slot (no sleep)

    worker = threading.Thread(target=limiter.wait, args=("arxiv",))
    worker.start()
    time.sleep(0.05)  # let the arXiv waiter enter its sleep

    started = time.monotonic()
    limiter.wait("openalex")
    elapsed = time.monotonic() - started
    worker.join(timeout=2.0)

    assert elapsed < 0.2, f"openalex was blocked for {elapsed:.2f}s by arxiv"
    assert not worker.is_alive()


def test_same_key_concurrent_calls_are_serialised():
    limiter = RateLimiter(intervals={"k": 0.05})
    threads = [threading.Thread(target=limiter.wait, args=("k",)) for _ in range(5)]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
    elapsed = time.monotonic() - started
    # 5 calls -> 4 gaps of 50 ms.
    assert elapsed >= 0.18
    assert elapsed < 2.0


def test_zero_interval_returns_immediately():
    limiter = RateLimiter(intervals={"free": 0.0})
    started = time.monotonic()
    for _ in range(50):
        limiter.wait("free")
    assert time.monotonic() - started < 0.1
