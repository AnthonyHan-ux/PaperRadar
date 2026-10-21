"""arXiv Atom API provider.

Docs: https://info.arxiv.org/help/api/user-manual.html
arXiv asks for >= 3 s between requests; the shared limiter enforces that.

arXiv is the single most important provider for this tool because it is the one
source that always carries a legal, directly downloadable PDF.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional
from urllib.parse import quote

from models.paper import OaStatus, Paper, PdfCandidate, SearchMode
from providers.base import ProviderError, SearchProvider, SearchQuery
from utils.title_normalizer import clean_text_block

log = logging.getLogger(__name__)

BASE_URL = "https://export.arxiv.org/api/query"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]+")
_STOP = {
    "the", "and", "for", "with", "from", "into", "using", "based", "via",
    "towards", "toward", "under", "over", "between", "through", "without",
    "using", "our", "your", "that", "this", "these", "those", "all", "any",
}


def _text(element: Optional[ET.Element]) -> str:
    if element is None or element.text is None:
        return ""
    return clean_text_block(element.text)


def _entry_id_to_arxiv(raw: str) -> Optional[str]:
    if not raw:
        return None
    match = re.search(r"abs/([^v\s]+?)(?:v\d+)?$", raw.strip())
    if match:
        return match.group(1)
    return None


def parse_entry(entry: ET.Element, source: str = "arxiv") -> Optional[Paper]:
    title = _text(entry.find("a:title", NS))
    if not title:
        return None

    raw_id = _text(entry.find("a:id", NS))
    arxiv_id = _entry_id_to_arxiv(raw_id)

    authors = [
        _text(node)
        for node in entry.findall("a:author/a:name", NS)
        if _text(node)
    ]

    published = _text(entry.find("a:published", NS))
    year: Optional[int] = None
    if published[:4].isdigit():
        year = int(published[:4])

    journal_ref = _text(entry.find("arxiv:journal_ref", NS))
    doi = _text(entry.find("arxiv:doi", NS))
    comment = _text(entry.find("arxiv:comment", NS))
    venue = journal_ref or ("arXiv preprint" if not doi else "arXiv")

    categories = [
        node.get("term", "") for node in entry.findall("a:category", NS) if node.get("term")
    ]

    pdf_url = ""
    for link in entry.findall("a:link", NS):
        if link.get("title") == "pdf":
            pdf_url = link.get("href", "")
            break
    if arxiv_id:
        # Canonical, version-less PDF URL so that the same paper found through
        # arXiv and through the OA finder dedupes to a single candidate.
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    elif not pdf_url:
        pdf_url = ""

    candidates = [PdfCandidate(url=pdf_url, provider="arXiv", is_oa=True)] if pdf_url else []

    return Paper(
        title=title,
        authors=authors,
        year=year,
        publication_date=published[:10],
        date_kind='submitted',
        venue=venue,
        doi=doi or None,
        arxiv_id=arxiv_id,
        abstract=_text(entry.find("a:summary", NS)),
        keywords=categories,
        url=raw_id or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None),
        pdf_candidates=candidates,
        oa_status=OaStatus.OPEN_ACCESS,
        sources=[source],
        citation_count=None,  # arXiv has no citation counts
        source_url=raw_id or None,
    )


class ArxivProvider(SearchProvider):
    name = "arxiv"
    display_name = "arXiv"
    modes = {
        SearchMode.KEYWORDS, SearchMode.TITLE, SearchMode.AUTHOR,
        SearchMode.ARXIV_ID, SearchMode.TOPIC,
    }

    # ------------------------------------------------------------------ #
    def _build_query(self, query: SearchQuery) -> str:
        text = query.text.replace('"', " ").strip()
        field = 'ti' if query.mode == SearchMode.TITLE else ('au' if query.mode == SearchMode.AUTHOR else 'all')
        if query.extra.get('match_mode') == 'fuzzy':
            tokens = [t for t in _TOKEN_RE.findall(text) if t.lower() not in _STOP]
            expression = ' AND '.join(f'{field}:{token}' for token in tokens) or f'{field}:"{text}"'
        else:
            expression = f'{field}:"{text}"'
        return self._with_dates(expression, query)

    @staticmethod
    def _with_dates(expression: str, query: SearchQuery) -> str:
        if query.start_date or query.end_date:
            lower = (query.start_date or '1991-01-01').replace('-', '') + '0000'
            upper = (query.end_date or '2100-12-31').replace('-', '') + '2359'
            return f'({expression}) AND submittedDate:[{lower} TO {upper}]'
        return expression

    @staticmethod
    def _fallback_query(query: SearchQuery) -> str:
        tokens = [
            token for token in _TOKEN_RE.findall(query.text)
            if token.lower() not in _STOP
        ]
        if not tokens:
            return ""
        field = "ti" if query.mode == SearchMode.TITLE else (
            "au" if query.mode == SearchMode.AUTHOR else "all"
        )
        return ArxivProvider._with_dates(" AND ".join(f"{field}:{token}" for token in tokens), query)

    def _fetch(self, search_query: str, query: SearchQuery, start: int = 0) -> list[ET.Element]:
        params: dict[str, Any] = {
            "search_query": search_query,
            "start": start,
            "max_results": max(1, min(int(query.limit), 50)),
            "sortBy": "submittedDate" if query.mode == SearchMode.TOPIC else "relevance",
            "sortOrder": "descending",
        }
        xml_text = self.http.get_text(BASE_URL, params, limiter_key="arxiv")
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise ProviderError(f"arXiv returned malformed Atom: {exc}") from exc
        entries = root.findall("a:entry", NS)
        # arXiv reports API-level errors inside a 200 response.
        total = root.findtext("os:totalResults", default="", namespaces=NS)
        if not entries and total == "0":
            return []
        return entries

    def search(self, query: SearchQuery) -> list[Paper]:
        if query.mode == SearchMode.DOI:
            # arXiv is not a DOI resolver; the arXiv provider owns id lookups.
            return []
        if query.mode == SearchMode.ARXIV_ID:
            arxiv_id = query.text.strip()
            params = {"id_list": arxiv_id, "max_results": 1}
            xml_text = self.http.get_text(BASE_URL, params, limiter_key="arxiv")
            entries = ET.fromstring(xml_text).findall("a:entry", NS) if xml_text else []
        else:
            entries = self._fetch(self._build_query(query), query)
            # Phrase search is precise but misses papers that reorder terms;
            # add an AND-of-tokens pass when the phrase pass is thin.
            if query.extra.get('match_mode') != 'exact' and len(entries) < min(3, max(1, query.limit // 4)):
                fallback = self._fallback_query(query)
                if fallback and fallback != self._build_query(query):
                    log.debug("arXiv phrase search thin (%d), retrying %s",
                              len(entries), fallback)
                    extra = self._fetch(fallback, query)
                    if extra:
                        seen = {_text(e.find("a:id", NS)) for e in entries}
                        entries.extend(
                            e for e in extra if _text(e.find("a:id", NS)) not in seen
                        )

        papers: list[Paper] = []
        for entry in entries:
            paper = parse_entry(entry)
            if paper is not None:
                papers.append(paper)
        log.info("arXiv: %d results for %r", len(papers), query.text[:60])
        return papers

    def get_by_doi(self, doi: str) -> Optional[Paper]:
        """arXiv DOIs are rare; title lookup is the practical path."""
        return None
