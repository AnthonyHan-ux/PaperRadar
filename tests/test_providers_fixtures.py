"""Offline provider parser tests driven by recorded API responses.

The fixtures under ``tests/fixtures/`` are real, trimmed API responses (the
Unpaywall one is hand-written from the documented schema because the service
validates the contact email and no real address is available to this project).
Tests must never hit the network: if a provider changes its schema, these tests
are what should fail first.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from models.paper import OaStatus, PdfStatus
from providers.arxiv import NS, parse_entry
from providers.base import SearchQuery
from providers.crossref import parse_item
from providers.openalex import parse_work, reconstruct_abstract
from providers.openreview import parse_note
from providers.semantic_scholar import parse_paper
from providers.unpaywall import parse_record

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# OpenAlex
# --------------------------------------------------------------------------- #
def test_openalex_parses_recorded_work():
    payload = load_json("openalex.json")
    work = payload["results"][0]
    paper = parse_work(work)

    assert paper is not None
    assert paper.title
    assert paper.sources == ["openalex"]
    assert paper.year == work.get("publication_year")
    assert paper.doi
    assert paper.doi == paper.doi.lower()
    assert paper.authors
    assert paper.abstract


def test_openalex_abstract_reconstruction():
    inverted = {"We": [0], "study": [1], "cloud": [2], "removal": [3]}
    assert reconstruct_abstract(inverted) == "We study cloud removal"
    assert reconstruct_abstract(None) == ""


def test_openalex_handles_missing_and_null_fields():
    assert parse_work({}) is None
    assert parse_work({"title": None}) is None
    minimal = parse_work({"title": "A Title Without Anything Else"})
    assert minimal is not None
    assert minimal.year is None
    assert minimal.doi is None
    assert minimal.authors == []
    assert minimal.pdf_candidates == []


def test_openalex_closed_work_has_no_candidates():
    work = {
        "title": "Closed Access Work",
        "open_access": {"is_oa": False, "oa_status": "closed"},
        "locations": [
            {"is_oa": False, "pdf_url": "https://example.org/should-not-be-used.pdf"}
        ],
    }
    paper = parse_work(work)
    assert paper is not None
    assert paper.oa_status == OaStatus.CLOSED
    assert paper.pdf_candidates == []


# --------------------------------------------------------------------------- #
# Semantic Scholar
# --------------------------------------------------------------------------- #
def test_semantic_scholar_parses_recorded_paper():
    payload = load_json("semantic_scholar.json")
    data = payload["data"][0]
    paper = parse_paper(data)

    assert paper is not None
    assert paper.title
    assert paper.sources == ["semantic_scholar"]
    assert isinstance(paper.authors, list)
    external = data.get("externalIds") or {}
    if external.get("DOI"):
        assert paper.doi == external["DOI"].lower()
    if external.get("ArXiv"):
        assert paper.arxiv_id == external["ArXiv"]


def test_semantic_scholar_handles_nulls():
    assert parse_paper({}) is None
    assert parse_paper({"title": None}) is None
    paper = parse_paper({"title": "Only A Title", "openAccessPdf": None})
    assert paper is not None
    assert paper.pdf_candidates == []
    assert paper.oa_status == OaStatus.UNKNOWN


def test_semantic_scholar_uses_open_access_pdf():
    paper = parse_paper(
        {
            "title": "Open Paper",
            "openAccessPdf": {"url": "https://example.org/x.pdf", "status": "GREEN"},
        }
    )
    assert paper.pdf_candidates[0].url == "https://example.org/x.pdf"
    assert paper.oa_status == OaStatus.OPEN_ACCESS


# --------------------------------------------------------------------------- #
# Crossref
# --------------------------------------------------------------------------- #
def test_crossref_parses_recorded_item():
    payload = load_json("crossref.json")
    item = payload["message"]["items"][0]
    paper = parse_item(item)

    assert paper is not None
    assert paper.title
    assert paper.doi
    assert paper.doi == paper.doi.lower()
    assert paper.sources == ["crossref"]
    assert "<jats:" not in (paper.abstract or "")


def test_crossref_handles_missing_fields():
    assert parse_item({}) is None
    paper = parse_item({"title": ["A Bare Record"], "DOI": "10.1000/Bare"})
    assert paper is not None
    assert paper.year is None
    assert paper.authors == []
    assert paper.doi == "10.1000/bare"


# --------------------------------------------------------------------------- #
# arXiv
# --------------------------------------------------------------------------- #
def test_arxiv_parses_recorded_feed():
    xml = (FIXTURES / "arxiv.xml").read_text(encoding="utf-8")
    root = ET.fromstring(xml)
    entries = root.findall("a:entry", NS)
    assert entries, "fixture must contain at least one entry"

    paper = parse_entry(entries[0])
    assert paper is not None
    assert paper.title
    assert paper.arxiv_id == "2503.19406"
    assert paper.oa_status == OaStatus.OPEN_ACCESS
    # Version-less canonical URL, identical to the one the OA finder builds.
    assert paper.pdf_candidates[0].url == "https://arxiv.org/pdf/2503.19406"
    assert paper.sources == ["arxiv"]


def test_arxiv_handles_missing_title():
    entry = ET.fromstring("<entry xmlns='http://www.w3.org/2005/Atom'></entry>")
    assert parse_entry(entry) is None


# --------------------------------------------------------------------------- #
# Unpaywall
# --------------------------------------------------------------------------- #
def test_unpaywall_parses_open_record():
    record = load_json("unpaywall.json")
    paper = parse_record(record)

    assert paper is not None
    assert paper.doi == record["doi"]
    assert paper.oa_status == OaStatus.OPEN_ACCESS
    assert paper.pdf_candidates
    assert paper.pdf_candidates[0].url.endswith("2503.19406")
    assert paper.year == record["year"]


def test_unpaywall_closed_record():
    closed = {
        "doi": "10.1000/closed",
        "title": "A Closed Access Article",
        "year": 2020,
        "is_oa": False,
        "best_oa_location": None,
        "oa_locations": [],
        "z_authors": [],
    }
    paper = parse_record(closed)
    assert paper is not None
    assert paper.oa_status == OaStatus.CLOSED
    assert paper.pdf_candidates == []


def test_unpaywall_oa_without_pdf_url():
    record = {
        "doi": "10.1000/landing",
        "title": "Landing Page Only",
        "is_oa": True,
        "best_oa_location": {"url": "https://example.org/landing", "url_for_pdf": None},
        "oa_locations": [],
    }
    paper = parse_record(record)
    assert paper is not None
    assert paper.oa_status == OaStatus.FREE_TO_READ
    assert paper.pdf_candidates == []


# --------------------------------------------------------------------------- #
# OpenReview
# --------------------------------------------------------------------------- #
def test_openreview_parses_recorded_note():
    payload = load_json("openreview.json")
    note = payload["notes"][0]
    paper = parse_note(note)

    assert paper is not None
    assert paper.title
    assert paper.pdf_candidates
    assert "/pdf?id=" in paper.pdf_candidates[0].url
    assert paper.oa_status == OaStatus.OPEN_ACCESS


def test_openreview_handles_missing_title():
    assert parse_note({}) is None
    assert parse_note({"content": {}}) is None


# --------------------------------------------------------------------------- #
# Provider wiring (query construction + parsing through the provider layer)
# --------------------------------------------------------------------------- #
class FakeHttp:
    def __init__(self, payload=None, text=None):
        self.payload = payload
        self.text = text
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, url, params=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        return self.payload

    def get_text(self, url, params=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        return self.text


def test_openalex_provider_builds_query_and_parses(settings):
    from providers.openalex import OpenAlexProvider

    http = FakeHttp(payload=load_json("openalex.json"))
    provider = OpenAlexProvider(settings, http)
    papers = provider.search(
        SearchQuery(text="change detection", year_from=2021, year_to=2026, limit=10)
    )

    assert papers
    assert http.calls
    url, params = http.calls[0]
    assert url.endswith("/works")
    assert params["search"] == "change detection"
    assert "from_publication_date:2021-01-01" in params["filter"]
    assert "to_publication_date:2026-12-31" in params["filter"]


def test_arxiv_provider_parses_feed(settings):
    from providers.arxiv import ArxivProvider

    http = FakeHttp(text=(FIXTURES / "arxiv.xml").read_text(encoding="utf-8"))
    provider = ArxivProvider(settings, http)
    papers = provider.search(SearchQuery(text="2503.19406", mode="arxiv_id"))

    assert papers
    assert papers[0].arxiv_id == "2503.19406"
    assert http.calls[0][1]["id_list"] == "2503.19406"
