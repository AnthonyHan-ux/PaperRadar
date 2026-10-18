"""Search orchestration tests with fake providers (no network)."""

from __future__ import annotations

import pytest

from models.paper import OaStatus, Paper, PdfCandidate, SearchMode
from providers.base import ProviderError, SearchProvider, SearchQuery
from services.relevance_service import RelevanceService
from services.search_service import SearchService


class FakeProvider(SearchProvider):
    def __init__(self, name: str, papers=None, error: str = ""):
        self.name = name
        self.display_name = name.upper()
        self._papers = list(papers or [])
        self._error = error

    def search(self, query: SearchQuery) -> list[Paper]:
        if self._error:
            raise ProviderError(self._error, status=500)
        return [p.copy() for p in self._papers]


def _make_service(settings, providers: dict) -> SearchService:
    settings.config["search"]["providers"] = list(providers.keys())
    return SearchService(
        settings, http=None, providers=providers, relevance=RelevanceService(settings)
    )


def _paper(title: str, **kwargs) -> Paper:
    defaults = dict(authors=["A. Author"], year=2025)
    defaults.update(kwargs)
    return Paper(title=title, **defaults)


def test_merges_duplicate_records_across_providers(settings):
    a = _paper(
        "Optical SAR Change Detection", doi="10.1109/TGRS.2025.1",
        sources=["openalex"], abstract="short",
    )
    b = _paper(
        "Optical SAR Change Detection", doi="10.1109/TGRS.2025.1",
        sources=["crossref"], abstract="a much longer abstract " * 5,
    )
    service = _make_service(settings, {
        "a": FakeProvider("a", [a]),
        "b": FakeProvider("b", [b]),
    })
    outcome = service.search(SearchQuery(text="optical SAR", limit=10))
    assert len(outcome.papers) == 1
    assert outcome.stats.duplicates_removed == 1
    assert len(outcome.papers[0].abstract) > 50


def test_provider_failure_is_isolated(settings):
    good = _paper("Robust Fusion for Disaster Response", doi="10.1000/good")
    service = _make_service(settings, {
        "good": FakeProvider("good", [good]),
        "bad": FakeProvider("bad", error="boom"),
    })
    outcome = service.search(SearchQuery(text="fusion", limit=5))
    assert len(outcome.papers) == 1
    errors = dict(outcome.errors())
    assert "BAD" in errors
    assert "boom" in errors["BAD"]
    assert any(report.ok for report in outcome.reports)


def test_year_filter_is_applied(settings):
    old = _paper("Old Paper on Change Detection", year=2015, doi="10.1000/old")
    new = _paper("New Paper on Change Detection", year=2025, doi="10.1000/new")
    service = _make_service(settings, {"p": FakeProvider("p", [old, new])})
    outcome = service.search(
        SearchQuery(text="change detection", year_from=2020, year_to=2026, limit=10)
    )
    assert [p.year for p in outcome.papers] == [2025]


def test_venue_filter_is_applied(settings):
    cvpr = _paper("Change Detection in Remote Sensing", venue="CVPR", doi="10.1000/c")
    tgr = _paper("Change Detection in Remote Sensing", venue="IEEE TGRS", doi="10.1000/t")
    service = _make_service(settings, {"p": FakeProvider("p", [cvpr, tgr])})
    outcome = service.search(
        SearchQuery(text="change detection", venues=["CVPR"], limit=10)
    )
    assert len(outcome.papers) == 1
    assert "CVPR" in (outcome.papers[0].venue or "")


def test_direction_filter_is_applied(settings):
    relevant = _paper(
        "Change Detection with Optical SAR", abstract="optical SAR data", doi="10.1000/r"
    )
    irrelevant = _paper("Graph Neural Networks for Chemistry", doi="10.1000/i")
    service = _make_service(settings, {"p": FakeProvider("p", [relevant, irrelevant])})
    query = SearchQuery(text="remote sensing", limit=10)
    query.extra["directions"] = ["Optical-SAR"]
    outcome = service.search(query)
    assert len(outcome.papers) == 1
    assert outcome.papers[0].doi == "10.1000/r"


def test_results_are_scored_and_sorted(settings):
    strong = _paper(
        "Optical SAR Change Detection for Building Damage Assessment",
        abstract="Robust fusion under fog and cloud degradation.",
        venue="CVPR", year=2026, doi="10.1000/strong",
        keywords=["change detection"],
    )
    weak = _paper("Generic Image Classification", year=2024, doi="10.1000/weak")
    service = _make_service(settings, {"p": FakeProvider("p", [weak, strong])})
    outcome = service.search(SearchQuery(text="remote sensing", limit=10))
    assert outcome.papers[0].doi == "10.1000/strong"
    assert outcome.papers[0].relevance_score > outcome.papers[1].relevance_score
    assert outcome.papers[0].relevance_details


def test_identifier_search_limits_results(settings):
    paper = _paper("Single Known Paper", doi="10.1016/j.rse.2025.9")
    service = _make_service(settings, {"p": FakeProvider("p", [paper])})
    outcome = service.search(SearchQuery(text="10.1016/j.rse.2025.9", mode=SearchMode.DOI, limit=1))
    assert len(outcome.papers) == 1


def test_no_providers_enabled_returns_empty(settings):
    settings.config["search"]["providers"] = []
    service = SearchService(settings, http=None, providers={}, relevance=RelevanceService(settings))
    outcome = service.search(SearchQuery(text="anything"))
    assert outcome.papers == []


def test_summary_text_mentions_counts(settings):
    service = _make_service(settings, {
        "p": FakeProvider("p", [_paper("Some Remote Sensing Work", doi="10.1000/s")]),
    })
    outcome = service.search(SearchQuery(text="remote sensing"))
    text = outcome.summary_text()
    assert "1" in text


def test_empty_provider_results_do_not_crash(settings):
    service = _make_service(settings, {"empty": FakeProvider("empty", [])})
    outcome = service.search(SearchQuery(text="nothing"))
    assert outcome.papers == []
    assert outcome.reports[0].count == 0
