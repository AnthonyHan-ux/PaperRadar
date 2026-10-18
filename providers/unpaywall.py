"""Unpaywall provider.

Docs: https://unpaywall.org/products/api
Unpaywall is the most reliable "is there a legal free copy?" oracle, but it
requires a contact email (it is free, non-commercial).  When no email is
configured the provider reports itself as unavailable instead of failing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from models.paper import OaStatus, Paper, PdfCandidate, SearchMode
from providers.base import ProviderError, SearchProvider, SearchQuery
from utils.title_normalizer import clean_text_block

log = logging.getLogger(__name__)

BASE_URL = "https://api.unpaywall.org/v2"


def _author_name(raw: dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""
    family = clean_text_block(raw.get("family") or "")
    given = clean_text_block(raw.get("given") or "")
    if family and given:
        return f"{given} {family}"
    return family or clean_text_block(raw.get("name") or "")


def _pdf_from_location(location: dict[str, Any]) -> Optional[str]:
    if not isinstance(location, dict):
        return None
    for key in ("url_for_pdf", "url"):
        value = location.get(key)
        if isinstance(value, str) and value.strip():
            if key == "url_for_pdf":
                return value.strip()
            if value.lower().split("?")[0].endswith(".pdf"):
                return value.strip()
    return None


def parse_record(data: dict[str, Any], source: str = "unpaywall") -> Optional[Paper]:
    if not isinstance(data, dict):
        return None
    title = clean_text_block(data.get("title") or "")
    if not title:
        return None

    candidates: list[PdfCandidate] = []
    seen: set[str] = set()
    best = data.get("best_oa_location")
    if isinstance(best, dict):
        url = _pdf_from_location(best)
        if url and url not in seen:
            seen.add(url)
            candidates.append(
                PdfCandidate(url=url, provider="Unpaywall", is_oa=True,
                             note=str(best.get("version") or ""))
            )
    for loc in data.get("oa_locations") or []:
        url = _pdf_from_location(loc)
        if url and url not in seen:
            seen.add(url)
            candidates.append(
                PdfCandidate(url=url, provider="Unpaywall", is_oa=True,
                             note=str(loc.get("version") or ""))
            )

    is_oa = bool(data.get("is_oa"))
    if candidates:
        oa_status = OaStatus.OPEN_ACCESS
    elif is_oa:
        oa_status = OaStatus.FREE_TO_READ
    else:
        oa_status = OaStatus.CLOSED

    doi = data.get("doi")
    authors = [_author_name(a) for a in data.get("z_authors") or [] if _author_name(a)]

    return Paper(
        title=title,
        authors=authors,
        year=data.get("year"),
        venue=clean_text_block(data.get("journal_name") or "") or None,
        doi=doi,
        abstract="",
        keywords=[],
        url=f"https://doi.org/{doi}" if doi else None,
        pdf_candidates=candidates,
        oa_status=oa_status,
        sources=[source],
        citation_count=None,
        source_url=data.get("doi_url") or (f"https://doi.org/{doi}" if doi else None),
    )


class UnpaywallProvider(SearchProvider):
    name = "unpaywall"
    display_name = "Unpaywall"
    modes = {SearchMode.DOI}

    def available(self) -> bool:
        return bool(self.settings.unpaywall_email)

    def unavailable_reason(self) -> str:
        return "Unpaywall 需要配置邮箱（config.yaml: api.unpaywall_email）"

    def search(self, query: SearchQuery) -> list[Paper]:
        if query.mode != SearchMode.DOI or not query.text.strip():
            return []
        doi = query.text.strip()
        email = self.settings.unpaywall_email
        if not email:
            return []
        params = {"email": email}
        try:
            data = self.http.get_json(
                f"{BASE_URL}/{doi}", params, limiter_key="unpaywall", retries=2
            )
        except ProviderError as exc:
            log.debug("Unpaywall lookup failed for %s: %s", doi, exc)
            return []
        paper = parse_record(data)
        return [paper] if paper else []

    def get_by_doi(self, doi: str) -> Optional[Paper]:
        results = self.search(SearchQuery(text=doi, mode=SearchMode.DOI, limit=1))
        return results[0] if results else None
