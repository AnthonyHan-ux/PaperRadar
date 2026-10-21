"""Retrieval matching only; never used for identity or deduplication."""
from __future__ import annotations

from rapidfuzz.fuzz import ratio
from utils.title_normalizer import normalize_text, content_tokens, contains_term


def text_matches(needle: str, text: str, fuzzy: bool = True) -> bool:
    query, haystack = normalize_text(needle), normalize_text(text)
    if not query or not haystack:
        return False
    if contains_term(haystack, query):
        return True
    if not fuzzy:
        return False
    words = haystack.split()
    terms = content_tokens(query)
    if not terms:
        return False
    def matches(term):
        if term in words:
            return True
        if len(term) >= 4 and any(term in word for word in words):
            return True
        return len(term) >= 5 and any(len(word) >= 5 and ratio(term, word) >= 85 for word in words)
    return all(matches(term) for term in terms)
