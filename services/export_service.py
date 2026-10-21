"""CSV / BibTeX / RIS export.

All writers take a list of :class:`~models.paper.Paper` and a destination path,
and return the path actually written (``.csv`` gets a UTF-8 BOM so Excel opens
Chinese titles correctly).
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Iterable, Optional

from models.paper import Paper

log = logging.getLogger(__name__)

CSV_FIELDS = [
    "title", "authors", "year", "venue", "doi", "arxiv_id",
    "oa_status", "pdf_status", "local_path", "relevance_score",
    "citation_count", "sources", "url", "abstract", 'publication_date', 'date_kind',
]

CONFERENCE_VENUES = {
    "cvpr", "iccv", "eccv", "neurips", "nips", "iclr", "aaai", "acm mm",
    "icra", "iros", "miccai", "wacv", "bmvc", "ijcai", "siggraph",
}

_BIBTEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _escape_bibtex(text: str) -> str:
    if not text:
        return ""
    out = []
    for char in text:
        out.append(_BIBTEX_SPECIAL.get(char, char))
    return "".join(out)


def _bibtex_key(paper: Paper, used: set[str]) -> str:
    surname = "anon"
    if paper.authors:
        first = paper.authors[0]
        surname = first.split(",")[0] if "," in first else first.split()[-1] if first.split() else "anon"
    surname = re.sub(r"[^A-Za-z]", "", surname).lower() or "anon"
    year = paper.year or "nd"
    word = "paper"
    for token in re.findall(r"[A-Za-z0-9]+", paper.title or ""):
        if len(token) > 2:
            word = token.lower()
            break
    base = f"{surname}{year}{word}"
    key = base
    counter = 1
    while key in used:
        counter += 1
        key = f"{base}{chr(96 + counter)}"
    used.add(key)
    return key


def _entry_type(paper: Paper) -> str:
    venue = (paper.venue or "").lower()
    for name in CONFERENCE_VENUES:
        if name in venue:
            return "inproceedings"
    if paper.arxiv_id and not paper.doi:
        return "misc"
    return "article"


def _csv_row(paper: Paper) -> dict[str, str]:
    return {
        "title": paper.title,
        "authors": "; ".join(paper.authors),
        "year": str(paper.year or ""),
        'publication_date':paper.publication_date or '',
        'date_kind':paper.date_kind,
        "venue": paper.venue or "",
        "doi": paper.doi or "",
        "arxiv_id": paper.arxiv_id or "",
        "oa_status": paper.oa_status.value,
        "pdf_status": paper.pdf_status.value,
        "local_path": paper.local_pdf_path or "",
        "relevance_score": "" if paper.relevance_score is None else f"{paper.relevance_score:.1f}",
        "citation_count": "" if paper.citation_count is None else str(paper.citation_count),
        "sources": ",".join(paper.sources),
        "url": paper.source_url or paper.url or "",
        "abstract": paper.abstract or "",
    }


class ExportService:
    # ------------------------------------------------------------------ #
    def to_csv(self, papers: Iterable[Paper], path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for paper in papers:
                writer.writerow(_csv_row(paper))
        log.info("exported %s", path)
        return path

    # ------------------------------------------------------------------ #
    def to_bibtex(self, papers: Iterable[Paper], path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        chunks: list[str] = []
        for paper in papers:
            entry_type = _entry_type(paper)
            key = _bibtex_key(paper, used)
            fields: list[tuple[str, str]] = []
            fields.append(("title", _escape_bibtex(paper.title)))
            if paper.authors:
                fields.append(("author", " and ".join(_escape_bibtex(a) for a in paper.authors)))
            if paper.year:
                fields.append(("year", str(paper.year)))
            if paper.venue:
                venue_field = "booktitle" if entry_type == "inproceedings" else "journal"
                fields.append((venue_field, _escape_bibtex(paper.venue)))
            if paper.doi:
                fields.append(("doi", _escape_bibtex(paper.doi)))
            if paper.arxiv_id:
                fields.append(("eprint", paper.arxiv_id))
                fields.append(("archivePrefix", "arXiv"))
            if paper.source_url or paper.url:
                fields.append(("url", _escape_bibtex(paper.source_url or paper.url or "")))
            if paper.abstract:
                fields.append(("abstract", _escape_bibtex(paper.abstract)))
            if paper.local_pdf_path:
                fields.append(("file", _escape_bibtex(paper.local_pdf_path)))

            body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
            chunks.append(f"@{entry_type}{{{key},\n{body}\n}}\n")

        path.write_text("\n".join(chunks), encoding="utf-8")
        log.info("exported %s", path)
        return path

    # ------------------------------------------------------------------ #
    def to_ris(self, papers: Iterable[Paper], path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for paper in papers:
            entry_type = "CPAPER" if _entry_type(paper) == "inproceedings" else "JOUR"
            lines.append(f"TY  - {entry_type}")
            lines.append(f"TI  - {paper.title}")
            for author in paper.authors:
                lines.append(f"AU  - {author}")
            if paper.year:
                lines.append(f"PY  - {paper.year}")
            if paper.venue:
                lines.append(f"JO  - {paper.venue}")
            if paper.doi:
                lines.append(f"DO  - {paper.doi}")
            if paper.arxiv_id:
                lines.append(f"AN  - arXiv:{paper.arxiv_id}")
            url = paper.source_url or paper.url
            if url:
                lines.append(f"UR  - {url}")
            lines.append(f"N2  - OA: {paper.oa_status.value}; PDF: {paper.pdf_status.value}")
            if paper.local_pdf_path:
                lines.append(f"L1  - {paper.local_pdf_path}")
            lines.append("ER  -")
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        log.info("exported %s", path)
        return path

    # ------------------------------------------------------------------ #
    def export(self, papers: Iterable[Paper], fmt: str, path: Path) -> Path:
        fmt = (fmt or "").lower().lstrip(".")
        if fmt == "csv":
            return self.to_csv(papers, path)
        if fmt in ("bib", "bibtex"):
            return self.to_bibtex(papers, path)
        if fmt == "ris":
            return self.to_ris(papers, path)
        raise ValueError(f"不支持的导出格式：{fmt}")

    def default_filename(self, fmt: str) -> str:
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        suffix = {"csv": "csv", "bib": "bib", "bibtex": "bib", "ris": "ris"}.get(
            fmt.lower(), fmt.lower()
        )
        return f"paperradar_{stamp}.{suffix}"
