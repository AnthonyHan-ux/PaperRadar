"""Preserve bibliographic date precision rather than inventing month/day."""
from __future__ import annotations

import calendar
from datetime import date
import re
from typing import Optional


def normalize_publication_date(value) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    if 'T' in text:
        text = text.split('T', 1)[0]
    if not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', text):
        return None
    try:
        parts = [int(part) for part in text.split('-')]
        date(parts[0], parts[1] if len(parts)>1 else 1, parts[2] if len(parts)>2 else 1)
    except (ValueError, TypeError):
        return None
    return text


def date_from_parts(parts) -> Optional[str]:
    if not isinstance(parts, (list, tuple)) or not 1 <= len(parts) <= 3:
        return None
    try:
        numbers = [int(part) for part in parts]
        text = f'{numbers[0]:04d}' + ''.join(f'-{part:02d}' for part in numbers[1:])
    except (ValueError, TypeError):
        return None
    return normalize_publication_date(text)


def date_interval(value: Optional[str], year: Optional[int] = None):
    text = normalize_publication_date(value) or normalize_publication_date(year)
    if not text:
        return None
    parts = [int(part) for part in text.split('-')]
    if len(parts) == 3:
        return text, text
    month = parts[1] if len(parts) == 2 else 1
    end_month = parts[1] if len(parts) == 2 else 12
    return (date(parts[0], month, 1).isoformat(),
            date(parts[0], end_month, calendar.monthrange(parts[0], end_month)[1]).isoformat())


def date_match(paper, query) -> str:
    interval = date_interval(paper.publication_date, paper.year)
    if interval is None:
        return 'unknown'
    start, end = interval
    lower, upper = query.start_date or '0001-01-01', query.end_date or '9999-12-31'
    if end < lower or start > upper:
        return 'outside'
    if start >= lower and end <= upper:
        return 'inside'
    return 'uncertain'
