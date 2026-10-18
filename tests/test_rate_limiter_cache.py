"""Rate limiter and JSON cache tests."""

from __future__ import annotations

import time

from utils.rate_limiter import JsonCache, RateLimiter


def test_rate_limiter_enforces_interval():
    limiter = RateLimiter(intervals={"test": 0.05})
    start = time.monotonic()
    limiter.wait("test")
    limiter.wait("test")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.045


def test_rate_limiter_independent_keys():
    limiter = RateLimiter(intervals={"a": 0.2, "b": 0.0})
    limiter.wait("a")
    start = time.monotonic()
    limiter.wait("b")
    assert time.monotonic() - start < 0.05


def test_rate_limiter_backoff_is_capped():
    limiter = RateLimiter(max_backoff=8.0)
    assert limiter.backoff(0) <= 8.0
    assert limiter.backoff(10) <= 8.0
    assert limiter.backoff(1) >= 1.0


def test_rate_limiter_honours_retry_after():
    limiter = RateLimiter(max_backoff=30.0)
    assert limiter.backoff(0, retry_after=3.0) == 3.0
    assert limiter.backoff(0, retry_after=100.0) <= 30.0


def test_json_cache_roundtrip(tmp_path):
    cache = JsonCache(tmp_path, ttl_seconds=60)
    cache.set("key", {"a": 1})
    assert cache.get("key") == {"a": 1}


def test_json_cache_expiry(tmp_path):
    cache = JsonCache(tmp_path, ttl_seconds=60)
    cache.set("key", {"a": 1})
    assert cache.get("key", ttl=0) is None


def test_json_cache_missing_key(tmp_path):
    cache = JsonCache(tmp_path, ttl_seconds=60)
    assert cache.get("nope") is None


def test_json_cache_survives_corruption(tmp_path):
    cache = JsonCache(tmp_path, ttl_seconds=60)
    cache.set("key", {"a": 1})
    path = next(tmp_path.glob("*.json"))
    path.write_text("{not json", encoding="utf-8")
    assert cache.get("key") is None
    cache.set("key", {"b": 2})
    assert cache.get("key") == {"b": 2}


def test_json_cache_clear(tmp_path):
    cache = JsonCache(tmp_path, ttl_seconds=60)
    cache.set("k1", 1)
    cache.set("k2", 2)
    assert cache.clear() == 2


def test_json_cache_disabled(tmp_path):
    cache = JsonCache(tmp_path / "sub", ttl_seconds=60, enabled=False)
    cache.set("k", 1)
    assert cache.get("k") is None
