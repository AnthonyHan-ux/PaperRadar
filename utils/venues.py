"""Venue canonicalisation.

Provider venue strings are messy (``"2024 IEEE/CVF Conference on Computer
Vision and Pattern Recognition (CVPR)"``, ``"CVPR"``, ``"Proceedings of ..."``).
This module maps them onto a small canonical set used for folder names, the
venue filter and the scoring bonus.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from utils.title_normalizer import normalize_text

log = logging.getLogger(__name__)

# canonical -> (folder slug, aliases)
VENUE_MAP: dict[str, dict[str, object]] = {
    "CVPR": {
        "slug": "CVPR",
        "aliases": [
            "cvpr", "computer vision and pattern recognition",
            "ieee/cvf conference on computer vision",
        ],
    },
    "ICCV": {
        "slug": "ICCV",
        "aliases": ["iccv", "international conference on computer vision"],
    },
    "ECCV": {
        "slug": "ECCV",
        "aliases": ["eccv", "european conference on computer vision"],
    },
    "NeurIPS": {
        "slug": "NeurIPS",
        "aliases": ["neurips", "nips", "neural information processing systems"],
    },
    "ICLR": {"slug": "ICLR", "aliases": ["iclr", "learning representations"]},
    "AAAI": {"slug": "AAAI", "aliases": ["aaai"]},
    "ACM MM": {
        "slug": "ACM_MM",
        "aliases": ["acm mm", "acm multimedia", "multimedia conference"],
    },
    "IEEE TGRS": {
        "slug": "TGRS",
        "aliases": [
            "tgrs", "transactions on geoscience and remote sensing",
            "ieee transactions on geoscience",
        ],
    },
    "IEEE TIP": {
        "slug": "TIP",
        "aliases": ["tip", "transactions on image processing", "ieee transactions on image processing"],
    },
    "IEEE JSTARS": {
        "slug": "JSTARS",
        "aliases": [
            "jstars", "selected topics in applied earth observations",
            "ieee journal of selected topics in applied earth observations",
        ],
    },
    "IEEE GRSL": {
        "slug": "GRSL",
        "aliases": [
            "grsl", "geoscience and remote sensing letters",
            "ieee geoscience and remote sensing letters",
        ],
    },
    "ISPRS JPRS": {
        "slug": "ISPRS_JPRS",
        "aliases": [
            "isprs", "isprs journal", "photogrammetry and remote sensing",
            "isprs journal of photogrammetry",
        ],
    },
    "Remote Sensing of Environment": {
        "slug": "RSE",
        "aliases": ["remote sensing of environment", "rse"],
    },
    "Remote Sensing (MDPI)": {
        "slug": "Remote_Sensing_MDPI",
        "aliases": ["remote sensing", "remote sens"],
    },
    "JAG": {
        "slug": "JAG",
        "aliases": [
            "jag", "international journal of applied earth observation",
            "applied earth observation and geoinformation",
        ],
    },
    "Photogrammetric Engineering & Remote Sensing": {
        "slug": "PE_RS",
        "aliases": [
            "photogrammetric engineering",
            "photogrammetric engineering and remote sensing",
            "pe rs", "pe&rs",
        ],
    },
    "Information Fusion": {
        "slug": "Information_Fusion",
        "aliases": ["information fusion"],
    },
    "arXiv": {"slug": "arXiv", "aliases": ["arxiv", "corr"]},
    "Other": {"slug": "Other", "aliases": []},
}

_CANONICAL_ALIASES: list[tuple[str, str]] = [
    (alias, canonical)
    for canonical, meta in VENUE_MAP.items()
    for alias in meta["aliases"]  # type: ignore[union-attr]
]
# Longest aliases first so "acm multimedia" beats "acm".
_CANONICAL_ALIASES.sort(key=lambda pair: len(pair[0]), reverse=True)


def canonical_venue(venue: Optional[str]) -> str:
    """Map an arbitrary venue string to a canonical name (``Other`` fallback)."""
    if not venue:
        return ""
    norm = normalize_text(venue)
    if not norm:
        return ""
    for alias, canonical in _CANONICAL_ALIASES:
        alias_norm = normalize_text(alias)
        if not alias_norm:
            continue
        if len(alias_norm) <= 5:
            if re.search(r"(?<![0-9a-z])" + re.escape(alias_norm) + r"(?![0-9a-z])", norm):
                return canonical
        elif alias_norm in norm:
            return canonical
    log.debug("Unknown venue normalized to Other: %r", venue)
    return "Other"


def venue_slug(venue: Optional[str]) -> str:
    """Folder-safe venue component (``ISPRS_JPRS``, ``CVPR``, ``Other``)."""
    canonical = canonical_venue(venue)
    if not canonical:
        return "Other"
    meta = VENUE_MAP.get(canonical)
    if meta:
        return str(meta["slug"])
    return re.sub(r"[^0-9A-Za-z]+", "_", canonical).strip("_") or "Other"


def venue_matches(venue: Optional[str], selected: list[str]) -> bool:
    """True when the paper's venue is one of the selected canonical venues."""
    if not selected:
        return True
    if not venue:
        return False
    canonical = canonical_venue(venue)
    if canonical in selected:
        return True
    norm = normalize_text(venue)
    for item in selected:
        alias_norm = normalize_text(item)
        if alias_norm and alias_norm in norm:
            return True
    return False
