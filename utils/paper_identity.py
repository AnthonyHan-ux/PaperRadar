"""Conservative guards for title-based matching, shared by search and storage."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from utils.title_normalizer import normalize_text

if TYPE_CHECKING:
    from models.paper import Paper


def _author_key(author: str) -> tuple[str, str]:
    if ',' in author:
        family, given = author.split(',', 1)
        return normalize_text(family), normalize_text(given)[:1]
    parts = normalize_text(author).split()
    if len(parts) > 1:
        return parts[-1], parts[0][:1]
    return (parts[0], '') if parts else ('', '')


def title_match_compatible(first: Paper, second: Paper, *, fuzzy: bool = False) -> bool:
    """Title similarity cannot override conflicting identifiers or authors.

    Exact DOI/arXiv matches are handled by callers before this fallback. Missing
    metadata remains compatible with older preprint/journal matching behavior.
    """
    for name in ('doi', 'arxiv_id'):
        a, b = getattr(first, name), getattr(second, name)
        if a and b and a != b:
            return False
    if first.authors and second.authors:
        a = {_author_key(name) for name in first.authors if name.strip()}
        b = {_author_key(name) for name in second.authors if name.strip()}
        if a and b and not a.intersection(b):
            return False
    if fuzzy:
        a, b = normalize_text(first.title), normalize_text(second.title)
        if set(re.findall(r'\b\d+\b', a)) != set(re.findall(r'\b\d+\b', b)):
            return False
        pattern = r'\b(?:part|volume|vol|chapter|phase|stage)\s+([ivxlcdm]+|\d+)\b'
        if re.findall(pattern, a) != re.findall(pattern, b):
            return False
    return True
