"""OpenAlex provider.

Docs: https://docs.openalex.org/api-entities/works
No API key required; add ``openalex_mailto`` to join the polite pool.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

from models.paper import OaStatus, Paper, PdfCandidate, SearchMode
from providers.base import ProviderError, SearchProvider, SearchQuery
from utils.title_normalizer import clean_text_block, normalize_title, title_similarity

log = logging.getLogger(__name__)

BASE_URL = "https://api.openalex.org"
_SELECT = (
    "id,doi,title,display_name,publication_year,publication_date,type,"
    "authorships,primary_location,best_oa_location,locations,open_access,"
    "cited_by_count,abstract_inverted_index,keywords,referenced_works_count"
)


def reconstruct_abstract(inverted_index: Optional[dict[str, list[int]]]) -> str:
    """OpenAlex ships abstracts as ``{word: [positions]}``; rebuild the text."""
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in inverted_index.items():
        if not isinstance(indexes, (list, tuple)):
            continue
        for index in indexes:
            try:
                positions.append((int(index), word))
            except (TypeError, ValueError):
                continue
    positions.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in positions)


def _oa_status(work: dict[str, Any]) -> OaStatus:
    oa = work.get("open_access") or {}
    raw = str(oa.get("oa_status") or "").lower()
    if raw in ("gold", "green", "hybrid"):
        return OaStatus.OPEN_ACCESS
    if raw == "bronze":
        return OaStatus.FREE_TO_READ
    if raw == "closed":
        return OaStatus.CLOSED
    if oa.get("is_oa"):
        return OaStatus.OPEN_ACCESS
    return OaStatus.UNKNOWN


def _locations(work: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for key in ("best_oa_location",):
        loc = work.get(key)
        if isinstance(loc, dict):
            found.append(loc)
    for loc in work.get("locations") or []:
        if isinstance(loc, dict):
            found.append(loc)
    return found


def parse_work(work: dict[str, Any], source: str = "openalex") -> Optional[Paper]:
    """Convert one OpenAlex work dict into a Paper (``None`` if unusable)."""
    if not isinstance(work, dict):
        return None
    title = work.get("title") or work.get("display_name") or ""
    title = clean_text_block(title)
    if not title:
        return None

    authors: list[str] = []
    for authorship in work.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author") or {}
        name = author.get("display_name") or ""
        if name:
            authors.append(name)

    primary = work.get("primary_location") or {}
    source_info = primary.get("source") or {}
    venue = source_info.get("display_name") or ""

    candidates: list[PdfCandidate] = []
    seen: set[str] = set()
    for loc in _locations(work):
        pdf_url = loc.get("pdf_url")
        if not pdf_url or not isinstance(pdf_url, str) or pdf_url in seen:
            continue
        if not loc.get("is_oa", True):
            continue
        seen.add(pdf_url)
        candidates.append(
            PdfCandidate(url=pdf_url, provider="OpenAlex", is_oa=True,
                         note=str(loc.get("version") or ""))
        )

    open_access = work.get("open_access") or {}
    oa_url = open_access.get("oa_url")
    if isinstance(oa_url, str) and oa_url.lower().split("?")[0].endswith(".pdf") and oa_url not in seen:
        seen.add(oa_url)
        candidates.append(PdfCandidate(url=oa_url, provider="OpenAlex", is_oa=True))

    keywords = [
        clean_text_block(k.get("display_name", ""))
        for k in work.get("keywords") or []
        if isinstance(k, dict) and k.get("display_name")
    ]

    landing = primary.get("landing_page_url") if isinstance(primary, dict) else None
    url = landing or work.get("id")
    if not landing and work.get("doi"):
        url = work["doi"]

    paper = Paper(
        title=title,
        authors=authors,
        year=work.get("publication_year"),
        venue=clean_text_block(venue) or None,
        doi=work.get("doi"),
        abstract=clean_text_block(reconstruct_abstract(work.get("abstract_inverted_index"))),
        keywords=keywords,
        url=url,
        pdf_candidates=candidates,
        oa_status=_oa_status(work),
        sources=[source],
        citation_count=work.get("cited_by_count"),
        source_url=landing or work.get("id"),
    )
    return paper


class OpenAlexProvider(SearchProvider):
    name = "openalex"
    display_name = "OpenAlex"
    modes = {
        SearchMode.KEYWORDS, SearchMode.TITLE, SearchMode.AUTHOR,
        SearchMode.DOI, SearchMode.TOPIC,
    }

    def _base_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.settings.mailto:
            params["mailto"] = self.settings.mailto
        return params

    def search(self, query: SearchQuery) -> list[Paper]:
        if query.mode == SearchMode.DOI:
            return self._search_doi(query)
        if query.mode == SearchMode.ARXIV_ID:
            # OpenAlex has no arXiv-id lookup; arXiv provider owns that mode.
            return []

        limit = max(1, min(int(query.limit), 50))
        params = self._base_params()
        params["per-page"] = limit

        filters: list[str] = []
        if query.mode == SearchMode.TITLE:
            filters.append(f"title.search:{query.text}")
        elif query.mode == SearchMode.AUTHOR:
            params["search"] = query.text
        else:
            params["search"] = query.text

        if query.year_from:
            filters.append(f"from_publication_date:{int(query.year_from)}-01-01")
        if query.year_to:
            filters.append(f"to_publication_date:{int(query.year_to)}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        if query.mode in (SearchMode.KEYWORDS, SearchMode.TOPIC, SearchMode.TITLE):
            params["sort"] = "relevance_score:desc"

        data = self.http.get_json(f"{BASE_URL}/works", params,
                                  limiter_key="openalex", use_cache=True)
        results = data.get("results") or []
        papers: list[Paper] = []
        for work in results:
            paper = parse_work(work)
            if paper is None:
                continue
            if query.mode == SearchMode.AUTHOR and not self._author_matches(query.text, paper):
                continue
            papers.append(paper)
        log.info("OpenAlex: %d/%d results for %r", len(papers), len(results), query.text[:60])
        return papers[:limit]

    def _search_doi(self, query: SearchQuery) -> list[Paper]:
        doi = query.text.strip()
        if not doi:
            return []
        params = self._base_params()
        params["filter"] = f"doi:{doi}"
        params["per-page"] = 1
        data = self.http.get_json(f"{BASE_URL}/works", params, limiter_key="openalex")
        results = data.get("results") or []
        for work in results:
            paper = parse_work(work)
            if paper is not None:
                return [paper]
        return []

    @staticmethod
    def _author_matches(author: str, paper: Paper) -> bool:
        needle = normalize_title(author)
        if not needle:
            return True
        for name in paper.authors:
            norm = normalize_title(name)
            if needle in norm or norm in needle:
                return True
            if title_similarity(author, name) >= 88:
                return True
        return False
