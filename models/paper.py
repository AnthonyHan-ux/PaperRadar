"""Core domain model shared by every layer of PaperRadar.

Everything that talks to the outside world (providers, downloaders, database,
GUI) speaks in terms of :class:`Paper`.  Keeping this module free of Qt, httpx
and sqlite imports makes the rest of the codebase easy to unit-test.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional

from utils.title_normalizer import normalize_title
from utils.publication_dates import normalize_publication_date


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class OaStatus(str, Enum):
    """Open-access availability of the *record*, not of the download attempt."""

    UNKNOWN = "UNKNOWN"
    OPEN_ACCESS = "OPEN_ACCESS"   # gold / hybrid / green, confirmed OA
    FREE_TO_READ = "FREE_TO_READ"  # e.g. bronze: free on publisher site
    CLOSED = "CLOSED"             # no OA location known


class PdfStatus(str, Enum):
    """Lifecycle of the local PDF acquisition for one paper.

    The states are intentionally granular: the whole point of PaperRadar is to
    tell *why* a paper has no local PDF instead of collapsing everything into
    "failed".
    """

    NOT_CHECKED = "NOT_CHECKED"
    SEARCHING = "SEARCHING"
    OPEN_ACCESS_FOUND = "OPEN_ACCESS_FOUND"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"          # file on disk, validation not finished
    VALIDATED = "VALIDATED"            # file on disk, title matched
    TITLE_WARNING = "TITLE_WARNING"    # file on disk, title only partially matched
    PUBLIC_BUT_BLOCKED = "PUBLIC_BUT_BLOCKED"
    PAYWALLED_ONLY = "PAYWALLED_ONLY"
    NO_PUBLIC_COPY_FOUND = "NO_PUBLIC_COPY_FOUND"
    MISSING_LOCAL_FILE = "MISSING_LOCAL_FILE"  # DB path exists, file vanished
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    WRONG_PAPER = "WRONG_PAPER"
    INVALID_PDF = "INVALID_PDF"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_PDF_STATES

    @property
    def has_local_file(self) -> bool:
        return self in (
            PdfStatus.DOWNLOADED,
            PdfStatus.VALIDATED,
            PdfStatus.TITLE_WARNING,
        )


_TERMINAL_PDF_STATES = {
    PdfStatus.VALIDATED,
    PdfStatus.TITLE_WARNING,
    PdfStatus.PUBLIC_BUT_BLOCKED,
    PdfStatus.PAYWALLED_ONLY,
    PdfStatus.NO_PUBLIC_COPY_FOUND,
    PdfStatus.MISSING_LOCAL_FILE,
    PdfStatus.DOWNLOAD_FAILED,
    PdfStatus.WRONG_PAPER,
    PdfStatus.INVALID_PDF,
}


class SearchMode(str, Enum):
    TITLE = "title"
    KEYWORDS = "keywords"
    AUTHOR = "author"
    DOI = "doi"
    ARXIV_ID = "arxiv_id"
    TOPIC = "topic"


# --------------------------------------------------------------------------- #
# PDF candidates
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PdfCandidate:
    """A *legal* public PDF location for a paper.

    ``provider`` records which resolution layer found it so that the UI and the
    logs can explain where the file came from.
    """

    url: str
    provider: str
    is_oa: bool = True
    note: str = ""

    def __str__(self) -> str:  # pragma: no cover - debugging helper
        return f"{self.provider}: {self.url}"


# --------------------------------------------------------------------------- #
# Paper
# --------------------------------------------------------------------------- #
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
_ARXIV_RE = re.compile(
    r"(?:arxiv:|abs/|pdf/)?(?P<id>\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE
)


def clean_doi(value: Optional[str]) -> Optional[str]:
    """Normalise a DOI to bare ``10.xxxx/...`` form (no URL, no ``doi:``)."""
    if not value:
        return None
    text = value.strip()
    if re.match(r'^https?://(?:dx\.)?doi\.org/', text, re.I):
        text = unquote(urlsplit(text).path.lstrip('/'))
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.I)
    m = _DOI_RE.search(text)
    if not m:
        return None
    doi = m.group(0).rstrip('.,;')
    # Parentheses are legal DOI characters. Strip only unmatched wrappers.
    while doi.endswith(')') and doi.count(')') > doi.count('('):
        doi = doi[:-1]
    return doi.lower()


def clean_arxiv_id(value: Optional[str]) -> Optional[str]:
    """Normalise an arXiv identifier to ``YYMM.NNNNN`` (version stripped)."""
    if not value:
        return None
    text = value.strip()
    if "arxiv.org" in text.lower():
        text = text.rsplit("/", 1)[-1]
    m = _ARXIV_RE.search(text)
    if m:
        return m.group("id")
    # Legacy identifiers such as cs/0601001 or math.AG/0601001
    legacy = re.search(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7})", text, re.I)
    if legacy:
        return legacy.group(1)
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item:
            continue
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


@dataclass
class Paper:
    """A single bibliographic record.

    Instances are *mutable* because the merge step enriches records coming from
    several providers (one source may know the abstract, another the PDF link).
    """

    title: str
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    abstract: Optional[str] = None
    keywords: list[str] = field(default_factory=list)
    url: Optional[str] = None
    pdf_candidates: list[PdfCandidate] = field(default_factory=list)
    oa_status: OaStatus = OaStatus.UNKNOWN
    pdf_status: PdfStatus = PdfStatus.NOT_CHECKED
    sources: list[str] = field(default_factory=list)
    citation_count: Optional[int] = None
    local_pdf_path: Optional[str] = None
    relevance_score: Optional[float] = None
    relevance_band: str = ""
    relevance_details: list[dict[str, Any]] = field(default_factory=list)
    id: Optional[int] = None
    discovered_at: str = field(default_factory=_now)
    downloaded_at: Optional[str] = None
    source_url: Optional[str] = None
    publication_date: Optional[str] = None
    date_kind: str = 'published'

    # ------------------------------------------------------------------ #
    # Normalisation / derived values
    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        self.title = (self.title or "").strip()
        self.publication_date = normalize_publication_date(self.publication_date)
        if self.publication_date and self.year is None:
            self.year = int(self.publication_date[:4])
        self.doi = clean_doi(self.doi)
        self.arxiv_id = clean_arxiv_id(self.arxiv_id)
        self.authors = _dedupe_keep_order(self.authors)
        self.keywords = _dedupe_keep_order(self.keywords)
        self.sources = _dedupe_keep_order(self.sources)
        if not self.source_url:
            self.source_url = self.url
        if self.year is not None and not (1500 <= int(self.year) <= 2200):
            self.year = None

    @property
    def normalized_title(self) -> str:
        return normalize_title(self.title)

    @property
    def date_label(self) -> str:
        value = self.publication_date or str(self.year or '')
        precision = {4:'仅年份', 7:'精确到月', 10:'来源记录到日'}.get(len(value), '日期缺失')
        kind = '首投稿' if self.date_kind == 'submitted' else '发表'
        return f'{kind}：{value or "未知"}（{precision}）'

    @property
    def first_author(self) -> str:
        return self.authors[0] if self.authors else ""

    @property
    def oa_flag(self) -> bool:
        return self.oa_status in (OaStatus.OPEN_ACCESS, OaStatus.FREE_TO_READ)

    @property
    def pdf_urls(self) -> list[str]:
        return [c.url for c in self.pdf_candidates]

    @property
    def is_local(self) -> bool:
        return bool(self.local_pdf_path)

    @property
    def status_label(self) -> str:
        """Short human-readable state combining OA + acquisition state."""
        if self.pdf_status.is_terminal:
            return self.pdf_status.value
        if self.oa_status == OaStatus.CLOSED:
            return "PAYWALLED_ONLY"
        return self.pdf_status.value

    @property
    def authors_short(self) -> str:
        if not self.authors:
            return ""
        if len(self.authors) <= 3:
            return ", ".join(self.authors)
        return f"{self.authors[0]} et al."

    def pdf_stem(self) -> str:
        return f"{self.year or 'n.d.'}_{self.first_author or 'anon'}"

    # ------------------------------------------------------------------ #
    # Merge
    # ------------------------------------------------------------------ #
    def add_source(self, name: str) -> None:
        if name and name not in self.sources:
            self.sources.append(name)

    def add_candidates(self, candidates: Iterable[PdfCandidate]) -> None:
        seen = {c.url.strip() for c in self.pdf_candidates}
        for cand in candidates:
            url = (cand.url or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            self.pdf_candidates.append(cand)

    def merge_from(self, other: "Paper") -> "Paper":
        """Fill gaps in ``self`` using ``other``; ``self`` wins on conflicts."""
        if not self.title and other.title:
            self.title = other.title
        if len(other.title or "") > len(self.title or "") and other.sources:
            # Prefer the more complete title (publishers truncate with "…")
            if not self.title.endswith("…") and not self.title.endswith("..."):
                if normalize_title(other.title).startswith(
                    normalize_title(self.title)[:40]
                ):
                    self.title = other.title
        if not self.abstract and other.abstract:
            self.abstract = other.abstract
        elif (
            other.abstract
            and self.abstract
            and len(other.abstract) > len(self.abstract) * 1.5
        ):
            self.abstract = other.abstract
        if not self.year and other.year:
            self.year = other.year
        if other.publication_date:
            prefer_published = self.date_kind == 'submitted' and other.date_kind == 'published'
            same_date_more_precise = (self.date_kind == other.date_kind
                and self.publication_date and other.publication_date.startswith(self.publication_date)
                and len(other.publication_date) > len(self.publication_date))
            if not self.publication_date or prefer_published or same_date_more_precise:
                self.publication_date = other.publication_date
                self.date_kind = other.date_kind
                self.year = int(other.publication_date[:4])
        if not self.venue and other.venue:
            self.venue = other.venue
        if not self.doi and other.doi:
            self.doi = other.doi
        if not self.arxiv_id and other.arxiv_id:
            self.arxiv_id = other.arxiv_id
        if not self.url and other.url:
            self.url = other.url
        if not self.source_url and other.source_url:
            self.source_url = other.source_url
        if not self.authors and other.authors:
            self.authors = list(other.authors)
        elif other.authors and len(other.authors) > len(self.authors):
            self.authors = list(other.authors)
        if not self.keywords and other.keywords:
            self.keywords = list(other.keywords)
        if self.citation_count is None and other.citation_count is not None:
            self.citation_count = other.citation_count
        elif (
            other.citation_count is not None
            and self.citation_count is not None
            and other.citation_count > self.citation_count
        ):
            self.citation_count = other.citation_count
        if self.oa_status == OaStatus.UNKNOWN and other.oa_status != OaStatus.UNKNOWN:
            self.oa_status = other.oa_status
        self.add_candidates(other.pdf_candidates)
        for src in other.sources:
            self.add_source(src)
        if self.pdf_status == PdfStatus.NOT_CHECKED and other.pdf_status != PdfStatus.NOT_CHECKED:
            self.pdf_status = other.pdf_status
        if not self.local_pdf_path and other.local_pdf_path:
            self.local_pdf_path = other.local_pdf_path
        return self

    def copy(self, **changes: Any) -> "Paper":
        return replace(self, **changes)

    # ------------------------------------------------------------------ #
    # Serialisation (used by the JSON cache and by --json CLI output)
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "publication_date": self.publication_date,
            "date_kind": self.date_kind,
            "venue": self.venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "abstract": self.abstract,
            "keywords": list(self.keywords),
            "url": self.url,
            "source_url": self.source_url,
            "pdf_candidates": [
                {
                    "url": c.url,
                    "provider": c.provider,
                    "is_oa": c.is_oa,
                    "note": c.note,
                }
                for c in self.pdf_candidates
            ],
            "oa_status": self.oa_status.value,
            "pdf_status": self.pdf_status.value,
            "sources": list(self.sources),
            "citation_count": self.citation_count,
            "local_pdf_path": self.local_pdf_path,
            "relevance_score": self.relevance_score,
            "relevance_band": self.relevance_band,
            "relevance_details": list(self.relevance_details),
            "discovered_at": self.discovered_at,
            "downloaded_at": self.downloaded_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Paper":
        data = dict(data or {})
        cands = [
            PdfCandidate(
                url=c.get("url", ""),
                provider=c.get("provider", "unknown"),
                is_oa=bool(c.get("is_oa", True)),
                note=c.get("note", ""),
            )
            for c in data.pop("pdf_candidates", []) or []
            if c.get("url")
        ]
        data["oa_status"] = OaStatus(data.get("oa_status") or "UNKNOWN")
        data["pdf_status"] = PdfStatus(data.get("pdf_status") or "NOT_CHECKED")
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in allowed}
        paper = cls(**kwargs)
        paper.pdf_candidates = cands
        return paper

    def display_line(self) -> str:  # pragma: no cover - convenience
        bits = [self.title[:90]]
        if self.year:
            bits.append(str(self.year))
        if self.venue:
            bits.append(self.venue)
        return " | ".join(bits)
