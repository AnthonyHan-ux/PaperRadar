"""Create a V1.0-layout data sandbox for end-to-end migration testing.

    python tools/make_legacy_sandbox.py <target_dir> [--papers N]

The sandbox mimics an installed V1.0 program directory:

    <target>/database/paperradar.db      (schema v1: no title_key, no http_status)
    <target>/library/2025/TGRS/*.pdf
    <target>/config/research_profile.yaml
    <target>/config/config.yaml          (V1.0 user config)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '[]',
    year INTEGER,
    venue TEXT,
    doi TEXT,
    arxiv_id TEXT,
    abstract TEXT,
    keywords TEXT NOT NULL DEFAULT '[]',
    oa_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    pdf_status TEXT NOT NULL DEFAULT 'NOT_CHECKED',
    local_path TEXT,
    source_url TEXT,
    pdf_urls TEXT NOT NULL DEFAULT '[]',
    sources TEXT NOT NULL DEFAULT '[]',
    citation_count INTEGER,
    relevance_score REAL,
    notes TEXT,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    downloaded_at TEXT,
    checked_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_doi
    ON papers(doi) WHERE doi IS NOT NULL AND doi <> '';
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def build(target: Path, papers: int) -> dict:
    target.mkdir(parents=True, exist_ok=True)
    db_path = target / "database" / "paperradar.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(V1_SCHEMA)
        for index in range(papers):
            conn.execute(
                "INSERT INTO papers (title, normalized_title, year, venue, doi,"
                " pdf_status, local_path, discovered_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"Legacy Sandbox Paper {index}",
                    f"legacy sandbox paper {index}",
                    2025,
                    "IEEE TGRS",
                    f"10.1000/sandbox.{index}",
                    "VALIDATED",
                    f"library/2025/TGRS/legacy_{index}.pdf",
                    "2025-01-01T00:00:00+00:00",
                    "2025-01-01T00:00:00+00:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    library = target / "library" / "2025" / "TGRS"
    library.mkdir(parents=True, exist_ok=True)
    for index in range(papers):
        (library / f"legacy_{index}.pdf").write_bytes(
            f"%PDF-1.4\n% legacy sandbox file {index}\n%%EOF\n".encode("ascii")
        )

    config = target / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "research_profile.yaml").write_text(
        "name: Migrated From V1.0\nkeywords:\n  - sandbox keyword\n",
        encoding="utf-8",
    )
    (config / "config.yaml").write_text(
        "api:\n  openalex_mailto: sandbox@example.com\n", encoding="utf-8"
    )

    return {
        "root": str(target),
        "papers": papers,
        "pdfs": len(list(library.glob("*.pdf"))),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--papers", type=int, default=3)
    args = parser.parse_args()
    info = build(Path(args.target), args.papers)
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
