"""Discipline-neutral suggestion lists and explicit bilingual aliases."""
from __future__ import annotations

from functools import lru_cache
import yaml

from utils.config import resource_root
from utils.title_normalizer import normalize_text


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    path = resource_root() / 'config/search_suggestions.yaml'
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (OSError, yaml.YAMLError):
        return {'areas':[], 'topics':[], 'venues':[]}
    return data


def suggestions(kind: str, area: str = 'all', custom=()) -> list[dict]:
    items = [dict(item) for item in load_catalog().get(kind, [])
             if area == 'all' or item.get('area') in (area, 'all')]
    known = {normalize_text(item['value']) for item in items}
    for term in custom:
        if term and normalize_text(str(term)) not in known:
            items.append({'label':str(term), 'value':str(term), 'aliases':[str(term)]})
            known.add(normalize_text(str(term)))
    return items


def find_suggestion(text: str, kind: str = 'topics'):
    key = normalize_text(text)
    if not key:
        return None
    for item in load_catalog().get(kind, []):
        if any(key == normalize_text(alias) for alias in [item['label'], item['value'], *item.get('aliases', [])]):
            return item
    return None


def resolve_term(text: str, kind: str = 'topics') -> str:
    item = find_suggestion(text, kind)
    return item['value'] if item else text.strip()


def topic_aliases(text: str) -> list[str]:
    item = find_suggestion(text)
    return list(dict.fromkeys([item['value'], *item.get('aliases', [])])) if item else [text]


def search_variants(text: str) -> list[str]:
    item = find_suggestion(text)
    return list(dict.fromkeys(item.get('search_terms') or [item['value']]))[:3] if item else [text]
