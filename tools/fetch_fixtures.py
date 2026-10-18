"""Refresh provider fixtures from the live APIs (developer tool, not a test).

    .\\.venv\\Scripts\\python.exe tools/fetch_fixtures.py

Network access is required.  The fixtures are trimmed to one record each and
stored under tests/fixtures/; the test suite itself never touches the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
UA = "PaperRadar/1.1 fixture-refresh (academic tool)"


def _get(url: str, params: dict | None = None) -> httpx.Response:
    with httpx.Client(timeout=40.0, follow_redirects=True,
                      headers={"User-Agent": UA}) as client:
        response = client.get(url, params=params)
    response.raise_for_status()
    return response


def fetch_openalex() -> dict:
    data = _get(
        "https://api.openalex.org/works",
        {
            "search": "optical SAR change detection",
            "per-page": 1,
            "select": (
                "id,doi,title,display_name,publication_year,publication_date,type,"
                "authorships,primary_location,best_oa_location,locations,open_access,"
                "cited_by_count,abstract_inverted_index,keywords,referenced_works_count"
            ),
        },
    ).json()
    return {
        "meta": {"count": data.get("meta", {}).get("count")},
        "results": data.get("results", [])[:1],
    }


def fetch_semantic_scholar() -> dict:
    fields = (
        "title,abstract,year,venue,publicationVenue,authors,externalIds,"
        "openAccessPdf,citationCount,url,publicationDate,fieldsOfStudy"
    )
    attempts = [
        ("https://api.semanticscholar.org/graph/v1/paper/ARXIV:2503.19406",
         {"fields": fields}),
        ("https://api.semanticscholar.org/graph/v1/paper/search",
         {"query": "optical SAR change detection", "limit": 1, "fields": fields}),
    ]
    last_error: Exception | None = None
    for _round in range(4):
        for url, params in attempts:
            try:
                data = _get(url, params).json()
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = exc
                continue
            if isinstance(data, dict) and "data" in data:
                return {"total": data.get("total"), "data": data.get("data", [])[:1]}
            return {"data": [data]}
        import time

        time.sleep(6)
    raise RuntimeError(f"Semantic Scholar unavailable: {last_error}")


def fetch_crossref() -> dict:
    data = _get(
        "https://api.crossref.org/works",
        {
            "query.bibliographic": "optical SAR change detection",
            "rows": 1,
            "select": (
                "DOI,title,author,issued,container-title,abstract,URL,"
                "is-referenced-by-count,type,link,event,publisher,short-container-title"
            ),
        },
    ).json()
    message = data.get("message", {})
    return {
        "status": data.get("status"),
        "message": {
            "total-results": message.get("total-results"),
            "items": message.get("items", [])[:1],
        },
    }


def fetch_arxiv() -> str:
    response = _get(
        "https://export.arxiv.org/api/query",
        {"id_list": "2503.19406", "max_results": 1},
    )
    return response.text


def fetch_unpaywall() -> dict:
    return _get(
        "https://api.unpaywall.org/v2/10.1109/lgrs.2025.3590959",
        {"email": "paperradar.fixtures@example.com"},
    ).json()


def fetch_openreview() -> dict:
    data = _get(
        "https://api2.openreview.net/notes/search",
        {
            "term": "remote sensing change detection",
            "type": "terms",
            "content": "all",
            "group": "all",
            "source": "forum",
            "limit": 1,
        },
    ).json()
    return {"count": data.get("count"), "notes": data.get("notes", [])[:1]}


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("openalex.json", fetch_openalex),
        ("semantic_scholar.json", fetch_semantic_scholar),
        ("crossref.json", fetch_crossref),
        ("arxiv.xml", fetch_arxiv),
        ("unpaywall.json", fetch_unpaywall),
        ("openreview.json", fetch_openreview),
    ]
    failures = 0
    for name, fetcher in jobs:
        try:
            payload = fetcher()
            target = FIXTURES / name
            if isinstance(payload, str):
                target.write_text(payload, encoding="utf-8")
            else:
                target.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            print(f"ok   {name}  {target.stat().st_size} bytes")
        except Exception as exc:  # noqa: BLE001 - developer tool
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
