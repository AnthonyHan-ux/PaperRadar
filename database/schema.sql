-- PaperRadar SQLite schema (V1)
-- Dedupe is enforced by three unique-ish keys: DOI, arXiv id and normalised
-- title.  SQLite treats NULLs as distinct, which is exactly what we want here.

CREATE TABLE IF NOT EXISTS papers (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT    NOT NULL,
    normalized_title  TEXT    NOT NULL,
    authors           TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    year              INTEGER,
    venue             TEXT,
    doi               TEXT,
    arxiv_id          TEXT,
    abstract          TEXT,
    keywords          TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    oa_status         TEXT    NOT NULL DEFAULT 'UNKNOWN',
    pdf_status        TEXT    NOT NULL DEFAULT 'NOT_CHECKED',
    local_path        TEXT,
    source_url        TEXT,
    pdf_urls          TEXT    NOT NULL DEFAULT '[]',   -- JSON [{url, provider, is_oa}]
    sources           TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    citation_count    INTEGER,
    relevance_score   REAL,
    notes             TEXT,
    discovered_at     TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    downloaded_at     TEXT,
    checked_at        TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_doi
    ON papers(doi) WHERE doi IS NOT NULL AND doi <> '';
CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_arxiv
    ON papers(arxiv_id) WHERE arxiv_id IS NOT NULL AND arxiv_id <> '';
CREATE INDEX IF NOT EXISTS ix_papers_norm_title ON papers(normalized_title);
CREATE INDEX IF NOT EXISTS ix_papers_year ON papers(year);
CREATE INDEX IF NOT EXISTS ix_papers_pdf_status ON papers(pdf_status);
CREATE INDEX IF NOT EXISTS ix_papers_discovered ON papers(discovered_at);

-- Which providers contributed to a record (provenance).
CREATE TABLE IF NOT EXISTS paper_sources (
    paper_id     INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    provider     TEXT    NOT NULL,
    source_url   TEXT,
    discovered_at TEXT   NOT NULL,
    PRIMARY KEY (paper_id, provider)
);

-- Free-form tags assigned by the user (V2 UI, table ready now).
CREATE TABLE IF NOT EXISTS paper_tags (
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    tag      TEXT    NOT NULL,
    created_at TEXT  NOT NULL,
    PRIMARY KEY (paper_id, tag)
);

CREATE TABLE IF NOT EXISTS search_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query        TEXT    NOT NULL,
    mode         TEXT    NOT NULL,
    filters      TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS download_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id   INTEGER,
    title      TEXT,
    url        TEXT,
    provider   TEXT,
    oa_status  TEXT,
    status     TEXT    NOT NULL,
    reason     TEXT,
    created_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_download_logs_created ON download_logs(created_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
