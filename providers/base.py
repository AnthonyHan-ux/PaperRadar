"""Provider abstraction.

Every metadata source implements :class:`SearchProvider` and returns
:class:`~models.paper.Paper` objects.  Nothing above this layer knows whether a
record came from OpenAlex, Crossref or a hand-written fixture, which is what
makes the merge step and the tests straightforward.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from models.paper import Paper, SearchMode


@dataclass
class SearchQuery:
    """A normalised user query, provider-independent."""

    text: str = ""
    mode: SearchMode = SearchMode.KEYWORDS
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    venues: list[str] = field(default_factory=list)
    limit: int = 30
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.text = (self.text or "").strip()
        if isinstance(self.mode, str):
            self.mode = SearchMode(self.mode)
        if self.limit is None or self.limit <= 0:
            self.limit = 30
        if self.year_from and self.year_to and self.year_from > self.year_to:
            self.year_from, self.year_to = self.year_to, self.year_from

    @property
    def is_identifier_query(self) -> bool:
        return self.mode in (SearchMode.DOI, SearchMode.ARXIV_ID)

    @property
    def year_filter(self) -> bool:
        return bool(self.year_from or self.year_to)


class ProviderError(Exception):
    """Base class for provider failures (network, HTTP, parse)."""

    def __init__(self, message: str, *, status: Optional[int] = None, url: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.url = url


class BlockedError(ProviderError):
    """401/403: the record exists but automated access is refused."""


class NotFoundError(ProviderError):
    """404: no such record."""


class RateLimitedError(ProviderError):
    """429 persisted after all retries."""


class NetworkError(ProviderError):
    """Timeouts, TLS failures, DNS problems."""


class SearchProvider(ABC):
    """Common interface for all metadata sources."""

    name: str = "provider"
    # Modes this provider can answer directly.  Others are emulated by search().
    modes: set[SearchMode] = {SearchMode.KEYWORDS, SearchMode.TITLE}
    # Human-readable weight used when the GUI explains where data came from.
    display_name: str = "Provider"

    def __init__(self, settings: Any, http: Any) -> None:
        self.settings = settings
        self.http = http

    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        """False disables the provider without failing the whole search."""
        return True

    def unavailable_reason(self) -> str:
        return ""

    @abstractmethod
    def search(self, query: SearchQuery) -> list[Paper]:
        """Return up to ``query.limit`` papers (may be fewer)."""

    def get_by_doi(self, doi: str) -> Optional[Paper]:
        query = SearchQuery(text=doi, mode=SearchMode.DOI, limit=1)
        try:
            results = self.search(query)
        except ProviderError:
            return None
        return results[0] if results else None

    def get_by_title(self, title: str) -> Optional[Paper]:
        query = SearchQuery(text=title, mode=SearchMode.TITLE, limit=1)
        try:
            results = self.search(query)
        except ProviderError:
            return None
        return results[0] if results else None

    def get_by_arxiv(self, arxiv_id: str) -> Optional[Paper]:
        query = SearchQuery(text=arxiv_id, mode=SearchMode.ARXIV_ID, limit=1)
        try:
            results = self.search(query)
        except ProviderError:
            return None
        return results[0] if results else None

    # ------------------------------------------------------------------ #
    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name}>"
