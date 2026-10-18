"""OpenReview provider.

Docs: https://docs.openreview.net/reference/api-v2
OpenReview is the official hosting platform for ICLR/NeurIPS submissions and
reviews, with a legal PDF endpoint (``/pdf?id=<forum>``).  It is therefore both
a metadata source and an OA-PDF source.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from models.paper import OaStatus, Paper, PdfCandidate, SearchMode
from providers.base import SearchProvider, SearchQuery
from utils.title_normalizer import clean_text_block

log = logging.getLogger(__name__)

BASE_URL = "https://api2.openreview.net"
PDF_TEMPLATE = "https://openreview.net/pdf?id={forum}"
FORUM_TEMPLATE = "https://openreview.net/forum?id={forum}"


def _value(content: dict[str, Any], key: str, default: Any = "") -> Any:
    """OpenReview v2 wraps every field as ``{"value": ...}``; v1 does not."""
    if not isinstance(content, dict):
        return default
    raw = content.get(key, default)
    if isinstance(raw, dict):
        return raw.get("value", default)
    return raw


def _year_from_note(note: dict[str, Any]) -> Optional[int]:
    for key in ("pdate", "cdate", "tcdate", "tmdate"):
        value = note.get(key)
        if isinstance(value, (int, float)) and value > 0:
            seconds = value / 1000.0 if value > 1e11 else float(value)
            try:
                return datetime.fromtimestamp(seconds, tz=timezone.utc).year
            except (OverflowError, OSError, ValueError):
                continue
    venue = str(_value(note.get("content") or {}, "venue"))
    for token in venue.split():
        if token.isdigit() and len(token) == 4:
            return int(token)
    return None


def parse_note(note: dict[str, Any], source: str = "openreview") -> Optional[Paper]:
    if not isinstance(note, dict):
        return None
    content = note.get("content") or {}
    title = clean_text_block(str(_value(content, "title") or ""))
    if not title:
        return None

    forum = note.get("forum") or note.get("id")
    authors_raw = _value(content, "authors") or []
    if isinstance(authors_raw, str):
        authors_raw = [part.strip() for part in authors_raw.split(",")]
    authors = [clean_text_block(str(a)) for a in authors_raw if a]

    abstract = clean_text_block(str(_value(content, "abstract") or ""))
    venue = clean_text_block(str(_value(content, "venue") or "")) or None
    keywords = _value(content, "keywords") or []
    if isinstance(keywords, str):
        keywords = [part.strip() for part in keywords.split(",")]

    candidates: list[PdfCandidate] = []
    if forum:
        candidates.append(
            PdfCandidate(url=PDF_TEMPLATE.format(forum=forum), provider="OpenReview", is_oa=True)
        )

    return Paper(
        title=title,
        authors=authors,
        year=_year_from_note(note),
        venue=venue,
        doi=None,
        arxiv_id=None,
        abstract=abstract,
        keywords=[clean_text_block(str(k)) for k in keywords if k],
        url=FORUM_TEMPLATE.format(forum=forum) if forum else None,
        pdf_candidates=candidates,
        oa_status=OaStatus.OPEN_ACCESS if candidates else OaStatus.UNKNOWN,
        sources=[source],
        citation_count=None,
        source_url=FORUM_TEMPLATE.format(forum=forum) if forum else None,
    )


class OpenReviewProvider(SearchProvider):
    name = "openreview"
    display_name = "OpenReview"
    modes = {SearchMode.KEYWORDS, SearchMode.TITLE, SearchMode.TOPIC}

    def search(self, query: SearchQuery) -> list[Paper]:
        if query.mode in (SearchMode.DOI, SearchMode.ARXIV_ID):
            return []
        if not query.text.strip():
            return []
        params = {
            "term": query.text,
            "type": "terms",
            "content": "all",
            "group": "all",
            "source": "forum",
            "limit": max(1, min(int(query.limit), 50)),
        }
        try:
            data = self.http.get_json(
                f"{BASE_URL}/notes/search", params, limiter_key="openreview", retries=2
            )
        except Exception as exc:  # OpenReview is best-effort, never fatal
            log.info("OpenReview search unavailable: %s", exc)
            return []

        papers: list[Paper] = []
        for note in data.get("notes") or []:
            paper = parse_note(note)
            if paper is not None:
                papers.append(paper)
        log.info("OpenReview: %d results for %r", len(papers), query.text[:60])
        return papers
