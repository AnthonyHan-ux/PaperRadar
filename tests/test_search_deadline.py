"""Search deadline tests: one hanging provider must not stall the search."""

from __future__ import annotations

import time

from models.paper import Paper
from providers.base import SearchProvider, SearchQuery
from services.relevance_service import RelevanceService
from services.search_service import SearchService


class SlowProvider(SearchProvider):
    def __init__(self, name: str, delay: float, papers=None) -> None:
        self.name = name
        self.display_name = name.upper()
        self.delay = delay
        self._papers = list(papers or [])

    def search(self, query: SearchQuery) -> list[Paper]:
        time.sleep(self.delay)
        return [p.copy() for p in self._papers]


class FastProvider(SlowProvider):
    def __init__(self, name: str, papers=None) -> None:
        super().__init__(name, 0.0, papers)


def _service(settings, providers):
    settings.config["search"]["providers"] = list(providers.keys())
    return SearchService(
        settings, http=None, providers=providers, relevance=RelevanceService(settings)
    )


def test_hanging_provider_cannot_extend_search(settings):
    settings.config["search"]["provider_timeout_seconds"] = 0.2
    fast_paper = Paper(title="Fast Optical SAR Result", doi="10.1000/fast", year=2025)
    service = _service(
        settings,
        {
            "slow": SlowProvider("slow", 1.5),
            "fast": FastProvider("fast", [fast_paper]),
        },
    )

    started = time.monotonic()
    outcome = service.search(SearchQuery(text="optical SAR", limit=5))
    elapsed = time.monotonic() - started

    # Budget is 0.2 * 1.5 = 0.3s; allow generous slack for CI scheduling.
    assert elapsed < 0.9, f"search took {elapsed:.2f}s despite the deadline"
    assert [p.doi for p in outcome.papers] == ["10.1000/fast"]

    slow = [r for r in outcome.reports if r.provider == "slow"]
    assert slow and slow[0].timed_out
    assert "provider_timeout" in slow[0].error

    fast = [r for r in outcome.reports if r.provider == "fast"]
    assert fast and fast[0].ok and fast[0].count == 1


def test_deadline_respects_provider_timeout_multiplier(settings):
    # 2.0 * 1.5 = 3.0s budget; the provider sleeps 0.1s so it must succeed.
    settings.config["search"]["provider_timeout_seconds"] = 2.0
    service = _service(
        settings,
        {"quick": SlowProvider("quick", 0.1, [Paper(title="Quick Paper", doi="10.1000/q")])},
    )
    started = time.monotonic()
    outcome = service.search(SearchQuery(text="quick", limit=5))
    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert len(outcome.papers) == 1
    assert not any(r.timed_out for r in outcome.reports)
