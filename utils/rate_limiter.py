"""Per-host throttling with exponential backoff and disk-backed JSON cache.

Academic APIs publish rate limits that are *not* uniform:

* OpenAlex   -> 10 req/s (polite pool with mailto)
* Crossref   -> 50 req/s polite pool, but bursts get 429
* S2         -> 1 req/s unauthenticated, aggressive 429
* arXiv      -> 3 s between requests, hard requirement
* Unpaywall  -> 100k/day, but be nice

Everything funnels through :class:`RateLimiter` so a slow provider cannot starve
the others (each provider uses its own *key*).
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe minimum-interval gate per key."""

    def __init__(
        self,
        default_interval: float = 0.0,
        intervals: Optional[dict[str, float]] = None,
        max_backoff: float = 32.0,
    ) -> None:
        self.default_interval = max(0.0, default_interval)
        self.intervals = {k: max(0.0, v) for k, v in (intervals or {}).items()}
        self.max_backoff = max_backoff
        self._lock = threading.Lock()
        # Reserved transmission slots: the gate never sleeps while holding the
        # lock, so a 3.2 s arXiv interval cannot stall OpenAlex/Crossref calls.
        self._next_allowed: dict[str, float] = {}

    def interval_for(self, key: str) -> float:
        return self.intervals.get(key, self.default_interval)

    def wait(self, key: str = "default") -> None:
        """Block until ``key`` may issue another request."""
        interval = self.interval_for(key)
        if interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            scheduled = max(now, self._next_allowed.get(key, 0.0))
            self._next_allowed[key] = scheduled + interval
        delay = scheduled - now
        if delay > 0:
            time.sleep(delay)

    def backoff(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Seconds to sleep before retry ``attempt`` (0-based), with jitter."""
        if retry_after is not None and retry_after > 0:
            return min(retry_after, self.max_backoff)
        delay = min(self.max_backoff, float(2 ** max(0, attempt)))
        # Jitter breaks thundering herds but must not exceed the cap.
        return min(self.max_backoff, delay * (0.9 + random.random() * 0.2))

    def sleep_backoff(self, attempt: int, retry_after: Optional[float] = None) -> float:
        delay = self.backoff(attempt, retry_after)
        log.debug("backing off %.2fs before retry #%d", delay, attempt + 1)
        time.sleep(delay)
        return delay


@dataclass
class CacheEntry:
    key: str
    created: float
    payload: Any


class JsonCache:
    """Tiny TTL cache for API responses (one JSON file per key).

    Keeps repeated searches instant and makes the GUI usable offline for records
    that were already fetched today.
    """

    def __init__(self, directory: Path, ttl_seconds: int = 24 * 3600, enabled: bool = True) -> None:
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        self._lock = threading.Lock()
        if self.enabled:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # pragma: no cover - defensive
                log.warning("cache disabled (%s)", exc)
                self.enabled = False

    def _path(self, key: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)[:160]
        return self.directory / f"{safe}.json"

    def get(self, key: str, ttl: Optional[int] = None) -> Optional[Any]:
        if not self.enabled:
            return None
        path = self._path(key)
        try:
            if not path.exists():
                return None
            with self._lock:
                raw = json.loads(path.read_text(encoding="utf-8"))
            max_age = self.ttl_seconds if ttl is None else ttl
            if max_age <= 0 or time.time() - float(raw.get("created", 0)) > max_age:
                return None
            return raw.get("payload")
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def set(self, key: str, payload: Any) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        try:
            data = json.dumps({"created": time.time(), "payload": payload}, ensure_ascii=False)
            with self._lock:
                tmp.write_text(data, encoding="utf-8")
                tmp.replace(path)
        except (OSError, TypeError, ValueError) as exc:  # pragma: no cover
            log.debug("cache write failed: %s", exc)

    def clear(self) -> int:
        removed = 0
        if not self.directory.exists():
            return 0
        for item in self.directory.glob("*.json"):
            try:
                item.unlink()
                removed += 1
            except OSError:
                pass
        return removed
