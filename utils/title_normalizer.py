"""Title / text normalisation and fuzzy comparison.

Titles arrive in wildly different shapes: publisher markup, HTML entities,
JATS fragments, unicode dashes, arXiv line breaks.  Everything that compares
titles (dedupe, relevance scoring, PDF validation) goes through this module so
that the comparison rules stay in one place.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Iterable, Optional

try:  # rapidfuzz is a hard dependency, but keep the module importable
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - only in broken environments
    fuzz = None  # type: ignore[assignment]


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Keep latin letters/digits and CJK; everything else becomes a separator.
_STRIP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff]+")

MATH_ENTITY_RE = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|#\d+|#x[0-9a-fA-F]+);")

# Words that carry no discriminative power when comparing titles.
_STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "in", "on", "to", "with",
    "by", "from", "at", "as", "is", "are", "via", "using", "towards",
    "toward", "under", "over", "into", "based",
}


def strip_markup(text: Optional[str]) -> str:
    """Remove HTML/JATS tags and decode entities."""
    if not text:
        return ""
    # Decode first so that HTML-escaped tags (&lt;b&gt;) are stripped as tags.
    cleaned = html.unescape(text)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = cleaned.replace("\u00ad", "")  # soft hyphen
    return cleaned


def normalize_text(text: Optional[str]) -> str:
    """Aggressive normalisation used for matching (lowercase, ascii-ish)."""
    if not text:
        return ""
    cleaned = strip_markup(text)
    cleaned = unicodedata.normalize("NFKC", cleaned).lower()
    # Fold accents (é -> e) so "Müller" and "Muller" compare equal.
    cleaned = unicodedata.normalize("NFKD", cleaned)
    cleaned = "".join(ch for ch in cleaned if not unicodedata.combining(ch))
    # Common ligatures / math symbols that survive NFKC inconsistently.
    cleaned = (
        cleaned.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    cleaned = _STRIP_RE.sub(" ", cleaned)
    return _WS_RE.sub(" ", cleaned).strip()


def normalize_title(title: Optional[str]) -> str:
    """Normalised title used as the dedupe key."""
    return normalize_text(title)


def tokens(text: Optional[str]) -> list[str]:
    return [t for t in normalize_text(text).split() if t]


def content_tokens(text: Optional[str]) -> list[str]:
    """Tokens with stopwords removed (better for short-title comparisons)."""
    return [t for t in tokens(text) if t not in _STOPWORDS]


def title_fingerprint(title: Optional[str], max_tokens: int = 3) -> str:
    """Short blocking key used to find fuzzy-duplicate candidates cheaply.

    Titles that name the same paper almost always share their leading content
    words ("convolutional global context ..."), so grouping by the first few
    tokens turns an O(n²) scan into a small bucket comparison.
    """
    if not title:
        return ""
    significant = content_tokens(title)
    if not significant:
        significant = tokens(title)
    return " ".join(significant[:max(1, max_tokens)])


def title_similarity(a: Optional[str], b: Optional[str]) -> float:
    """0-100 similarity between two titles.

    ``token_sort_ratio`` is robust to word order and to punctuation variants,
    which is exactly the failure mode we see between Crossref and arXiv titles.
    """
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    if fuzz is None:  # pragma: no cover
        return 100.0 if na == nb else 0.0
    return float(max(fuzz.token_sort_ratio(na, nb), fuzz.ratio(na, nb)))


def is_same_title(a: Optional[str], b: Optional[str], threshold: float = 95.0) -> bool:
    return title_similarity(a, b) >= threshold


def is_probably_same_title(a: Optional[str], b: Optional[str]) -> bool:
    """Looser comparison for metadata enrichment (>= 90)."""
    return title_similarity(a, b) >= 90.0


def contains_term(haystack_normalized: str, term: str) -> bool:
    """Whole-phrase containment on normalised text (word-boundary aware)."""
    if not haystack_normalized or not term:
        return False
    needle = normalize_text(term)
    if not needle:
        return False
    pattern = r"(?<![0-9a-z])" + re.escape(needle) + r"(?![0-9a-z])"
    return re.search(pattern, haystack_normalized) is not None


def snippet(text: Optional[str], limit: int = 240) -> str:
    """Collapse whitespace and truncate for table/tooltip display."""
    if not text:
        return ""
    clean = _WS_RE.sub(" ", strip_markup(text)).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def clean_text_block(text: Optional[str]) -> str:
    """Strip markup/entities and collapse whitespace without truncation.

    Used for abstracts before they are persisted, so the database never stores
    JATS fragments like ``<jats:p>``.
    """
    if not text:
        return ""
    clean = strip_markup(text)
    clean = clean.replace("\u00ad", "")
    return _WS_RE.sub(" ", clean).strip()


def unique_preserving_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item:
            continue
        key = normalize_text(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out
