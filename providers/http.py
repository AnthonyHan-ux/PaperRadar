"""Shared HTTP layer: one pooling client, polite throttling, retries, cache.

All providers go through :class:`HttpClient`.  The rules encoded here are the
ones that keep the tool alive against real APIs:

* never retry a 4xx except 429,
* honour ``Retry-After``,
* exponential backoff with jitter on 429/5xx/timeouts,
* cache successful JSON for a configurable TTL,
* map failures onto typed exceptions so the UI can explain them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any, Optional

import httpx

from providers.base import (
    BlockedError,
    NetworkError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
)
from utils.rate_limiter import JsonCache, RateLimiter

log = logging.getLogger(__name__)


def _hash_key(url: str, params: Optional[dict[str, Any]]) -> str:
    payload = url + "|" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class HttpClient:
    """Thread-safe JSON/HTTP client shared by every provider."""

    def __init__(
        self,
        user_agent: str,
        timeout: float = 30.0,
        max_retries: int = 3,
        limiter: Optional[RateLimiter] = None,
        cache: Optional[JsonCache] = None,
        default_headers: Optional[dict[str, str]] = None,
        verify: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.limiter = limiter or RateLimiter()
        self.cache = cache
        self.verify = verify
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, application/atom+xml, text/xml;q=0.9, */*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if default_headers:
            self._headers.update(default_headers)
        self._lock = threading.Lock()
        self._client: Optional[httpx.Client] = None
        self.request_count = 0

    # ------------------------------------------------------------------ #
    @property
    def client(self) -> httpx.Client:
        with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.Client(
                    timeout=httpx.Timeout(self.timeout, connect=min(15.0, self.timeout)),
                    follow_redirects=True,
                    headers=self._headers,
                    verify=self.verify,
                    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
                )
            return self._client

    def close(self) -> None:
        with self._lock:
            if self._client is not None and not self._client.is_closed:
                self._client.close()
            self._client = None

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        limiter_key: Optional[str] = None,
        retries: Optional[int] = None,
    ) -> httpx.Response:
        """Perform a request with throttling + retries; raise typed errors."""
        if limiter_key:
            key = limiter_key
        elif "//" in url:
            key = url.split("/")[2] or "default"
        else:
            key = "default"
        retries = self.max_retries if retries is None else retries
        last_error: Optional[Exception] = None

        for attempt in range(retries + 1):
            self.limiter.wait(key)
            try:
                self.request_count += 1
                response = self.client.request(method, url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError) as exc:
                last_error = exc
                log.debug("network error on %s (attempt %d): %s", url, attempt + 1, exc)
                if attempt < retries:
                    self.limiter.sleep_backoff(attempt)
                    continue
                raise NetworkError(f"{type(exc).__name__}: {exc}", url=url) from exc
            except httpx.HTTPError as exc:  # pragma: no cover - defensive
                raise NetworkError(str(exc), url=url) from exc

            status = response.status_code
            if status == 429:
                retry_after = self._retry_after(response)
                log.warning("429 from %s, retry_after=%s", url, retry_after)
                if attempt < retries:
                    self.limiter.sleep_backoff(attempt, retry_after)
                    continue
                raise RateLimitedError(f"429 Too Many Requests: {url}", status=status, url=url)
            if status in (401, 403):
                raise BlockedError(f"{status} access denied: {url}", status=status, url=url)
            if status == 404:
                raise NotFoundError(f"404 not found: {url}", status=status, url=url)
            if status == 400:
                raise ProviderError(f"400 bad request: {url}", status=status, url=url)
            if 500 <= status < 600:
                log.debug("server error %d on %s (attempt %d)", status, url, attempt + 1)
                if attempt < retries:
                    self.limiter.sleep_backoff(attempt)
                    continue
                raise ProviderError(f"{status} server error: {url}", status=status, url=url)
            if status >= 400:
                raise ProviderError(f"HTTP {status}: {url}", status=status, url=url)
            return response

        raise ProviderError(f"request failed: {last_error}", url=url)  # pragma: no cover

    @staticmethod
    def _retry_after(response: httpx.Response) -> Optional[float]:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    # ------------------------------------------------------------------ #
    def get_json(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        *,
        limiter_key: Optional[str] = None,
        cache_ttl: Optional[int] = None,
        use_cache: bool = True,
        headers: Optional[dict[str, str]] = None,
        retries: Optional[int] = None,
    ) -> dict[str, Any]:
        cache_key = _hash_key(url, params)
        if use_cache and self.cache is not None:
            cached = self.cache.get(cache_key, ttl=cache_ttl)
            if isinstance(cached, dict):
                log.debug("cache hit %s", url)
                return cached

        response = self.request(
            "GET", url, params=params, headers=headers,
            limiter_key=limiter_key, retries=retries,
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"invalid JSON from {url}: {response.text[:120]}", url=url
            ) from exc
        if not isinstance(data, dict):
            raise ProviderError(f"unexpected JSON root type from {url}", url=url)

        if use_cache and self.cache is not None:
            self.cache.set(cache_key, data)
        return data

    def get_text(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        *,
        limiter_key: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        retries: Optional[int] = None,
    ) -> str:
        response = self.request(
            "GET", url, params=params, headers=headers,
            limiter_key=limiter_key, retries=retries,
        )
        return response.text
