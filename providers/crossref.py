"""Crossref provider.

Docs: https://api.crossref.org/swagger-ui/index.html
Crossref is the authoritative DOI registry: it is the best source for publisher
venue names, volume/page data and author lists, but it rarely exposes a usable
open-access PDF (that is Unpaywall/OpenAlex territory).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from models.paper import Paper, SearchMode
from providers.base import ProviderError, SearchProvider, SearchQuery
from utils.title_normalizer import clean_text_block

log = logging.getLogger(__name__)

BASE_URL = "https://api.crossref.org"
SELECT = (
    "DOI,title,author,issued,container-title,abstract,URL,"
    "is-referenced-by-count,type,link,event,publisher,short-container-title"
)


def _year_from_item(item: dict[str, Any]) -> Optional[int]:
    for key in ("issued", "published-print", "published-online", "created"):
        date = item.get(key) or {}
        parts = date.get("date-parts") or []
        if parts and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _first(value: Any) -> str:
    if isinstance(value, list):
        return clean_text_block(str(value[0])) if value else ""
    if isinstance(value, str):
        return clean_text_block(value)
    return ""


def _format_author(author: dict[str, Any]) -> str:
    if not isinstance(author, dict):
        return ""
    family = clean_text_block(author.get("family") or "")
    given = clean_text_block(author.get("given") or "")
    name = clean_text_block(author.get("name") or "")
    if family and given:
        return f"{given} {family}"
    return family or name


def parse_item(item: dict[str, Any], source: str = "crossref") -> Optional[Paper]:
    if not isinstance(item, dict):
        return None
    title = _first(item.get("title"))
    if not title:
        return None

    venue = _first(item.get("container-title"))
    if not venue:
        event = item.get("event") or {}
        if isinstance(event, dict):
            venue = clean_text_block(event.get("name") or "")

    authors = [
        name for name in (_format_author(a) for a in item.get("author") or []) if name
    ]

    doi = item.get("DOI")
    url = item.get("URL")
    if doi and not url:
        url = f"https://doi.org/{doi}"

    return Paper(
        title=title,
        authors=authors,
        year=_year_from_item(item),
        venue=venue or None,
        doi=doi,
        abstract=clean_text_block(item.get("abstract") or ""),
        keywords=[],  # Crossref has no keyword field
        url=url,
        sources=[source],
        citation_count=item.get("is-referenced-by-count"),
        source_url=url,
    )


class CrossrefProvider(SearchProvider):
    name = "crossref"
    display_name = "Crossref"
    modes = {
        SearchMode.KEYWORDS, SearchMode.TITLE, SearchMode.AUTHOR,
        SearchMode.DOI, SearchMode.TOPIC,
    }

    def _params(self, query: SearchQuery) -> dict[str, Any]:
        params: dict[str, Any] = {
            "rows": max(1, min(int(query.limit), 50)),
            "select": SELECT,
            "sort": "relevance",
            "order": "desc",
        }
        if self.settings.mailto:
            params["mailto"] = self.settings.mailto

        date_filters: list[str] = []
        if query.year_from:
            date_filters.append(f"from-pub-date:{int(query.year_from)}-01-01")
        if query.year_to:
            date_filters.append(f"until-pub-date:{int(query.year_to)}-12-31")
        if date_filters:
            params["filter"] = ",".join(date_filters)
        return params

    def search(self, query: SearchQuery) -> list[Paper]:
        if query.mode == SearchMode.DOI:
            return self._by_doi(query)
        if query.mode == SearchMode.ARXIV_ID:
            # arXiv ids are not Crossref identifiers.
            return []

        params = self._params(query)
        if query.mode == SearchMode.TITLE:
            params["query.title"] = query.text
        elif query.mode == SearchMode.AUTHOR:
            params["query.author"] = query.text
        else:
            params["query.bibliographic"] = query.text

        data = self.http.get_json(f"{BASE_URL}/works", params, limiter_key="crossref")
        message = data.get("message") or {}
        items = message.get("items") or []
        papers: list[Paper] = []
        for item in items:
            paper = parse_item(item)
            if paper is not None:
                papers.append(paper)
        log.info("Crossref: %d results for %r", len(papers), query.text[:60])
        return papers

    def _by_doi(self, query: SearchQuery) -> list[Paper]:
        doi = query.text.strip()
        if not doi:
            return []
        params = {"select": SELECT}
        if self.settings.mailto:
            params["mailto"] = self.settings.mailto
        try:
            data = self.http.get_json(
                f"{BASE_URL}/works/{doi}", params, limiter_key="crossref", retries=2
            )
        except ProviderError as exc:
            log.debug("Crossref DOI lookup failed for %s: %s", doi, exc)
            return []
        message = data.get("message") or {}
        paper = parse_item(message)
        return [paper] if paper else []
