"""Semantic Scholar Graph API provider.

Docs: https://api.semanticscholar.org/api-docs/graph
Unauthenticated traffic is throttled to ~1 request/second and often answers
429; the shared limiter/backoff handles it.  An API key is optional and raises
the ceiling considerably.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from models.paper import OaStatus, Paper, PdfCandidate, SearchMode
from providers.base import SearchProvider, SearchQuery
from utils.title_normalizer import clean_text_block

log = logging.getLogger(__name__)

BASE_URL = "https://api.semanticscholar.org/graph/v1"
FIELDS = (
    "title,abstract,year,venue,publicationVenue,authors,externalIds,"
    "openAccessPdf,citationCount,url,publicationDate,fieldsOfStudy"
)


def parse_paper(data: dict[str, Any], source: str = "semantic_scholar") -> Optional[Paper]:
    if not isinstance(data, dict):
        return None
    title = clean_text_block(data.get("title") or "")
    if not title and data.get("paperId") is None:
        return None
    if not title:
        return None

    external = data.get("externalIds") or {}
    doi = external.get("DOI") if isinstance(external, dict) else None
    arxiv_id = external.get("ArXiv") if isinstance(external, dict) else None

    venue = clean_text_block(data.get("venue") or "") or None
    pub_venue = data.get("publicationVenue") or {}
    if not venue and isinstance(pub_venue, dict):
        venue = clean_text_block(pub_venue.get("name") or "") or None

    authors = [
        clean_text_block(a.get("name", ""))
        for a in data.get("authors") or []
        if isinstance(a, dict) and a.get("name")
    ]

    candidates: list[PdfCandidate] = []
    oa_pdf = data.get("openAccessPdf") or {}
    if isinstance(oa_pdf, dict) and oa_pdf.get("url"):
        candidates.append(
            PdfCandidate(
                url=str(oa_pdf["url"]),
                provider="Semantic Scholar",
                is_oa=True,
                note=str(oa_pdf.get("status") or ""),
            )
        )

    url = data.get("url")
    if doi:
        source_url = f"https://doi.org/{doi}"
    else:
        source_url = url

    return Paper(
        title=title,
        authors=authors,
        year=data.get("year"),
        publication_date=data.get('publicationDate'),
        venue=venue,
        doi=doi,
        arxiv_id=arxiv_id,
        abstract=clean_text_block(data.get("abstract") or ""),
        keywords=[clean_text_block(k) for k in data.get("fieldsOfStudy") or [] if k],
        url=source_url,
        pdf_candidates=candidates,
        oa_status=OaStatus.OPEN_ACCESS if candidates else OaStatus.UNKNOWN,
        sources=[source],
        citation_count=data.get("citationCount"),
        source_url=source_url,
    )


class SemanticScholarProvider(SearchProvider):
    name = "semantic_scholar"
    display_name = "Semantic Scholar"
    modes = {
        SearchMode.KEYWORDS, SearchMode.TITLE, SearchMode.AUTHOR,
        SearchMode.DOI, SearchMode.TOPIC,
    }

    def _headers(self) -> dict[str, str]:
        key = str(self.settings.api.get("semantic_scholar_api_key") or "").strip()
        return {"x-api-key": key} if key else {}

    def _params(self, query: SearchQuery) -> dict[str, Any]:
        params: dict[str, Any] = {
            "query": query.text,
            "limit": max(1, min(int(query.limit), 100)),
            "fields": FIELDS,
        }
        if query.date_from or query.date_to:
            params['publicationDateOrYear'] = f'{query.start_date or ""}:{query.end_date or ""}'
        elif query.year_from or query.year_to:
            start = int(query.year_from) if query.year_from else ""
            end = int(query.year_to) if query.year_to else ""
            params["year"] = f"{start}-{end}"
        return params

    def search(self, query: SearchQuery) -> list[Paper]:
        if query.mode == SearchMode.DOI:
            return self._by_identifier(f"DOI:{query.text}", query)
        if query.mode == SearchMode.ARXIV_ID:
            return self._by_identifier(f"ARXIV:{query.text}", query)

        data = self.http.get_json(
            f"{BASE_URL}/paper/search",
            self._params(query),
            limiter_key="semantic_scholar",
            headers=self._headers(),
            retries=2,  # unauthenticated S2 429s aggressively; don't burn 10s
        )
        papers: list[Paper] = []
        for item in data.get("data") or []:
            paper = parse_paper(item)
            if paper is not None:
                papers.append(paper)
        log.info("SemanticScholar: %d results for %r", len(papers), query.text[:60])
        return papers

    def _by_identifier(self, ident: str, query: SearchQuery) -> list[Paper]:
        params = {"fields": FIELDS}
        if query.year_from or query.year_to:
            params["year"] = f"{query.year_from or ''}-{query.year_to or ''}"
        try:
            data = self.http.get_json(
                f"{BASE_URL}/paper/{ident}",
                params,
                limiter_key="semantic_scholar",
                headers=self._headers(),
                retries=2,
            )
        except Exception as exc:
            log.debug("S2 identifier lookup failed for %s: %s", ident, exc)
            return []
        paper = parse_paper(data)
        return [paper] if paper else []
