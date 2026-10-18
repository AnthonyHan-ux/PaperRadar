"""Merge records coming from several providers into one de-duplicated list.

Dedupe priority (as specified): DOI -> arXiv id -> normalised title (exact) ->
fuzzy title similarity.  The merged record keeps the best field from every
source, which is why a Crossref shell can end up with an arXiv PDF.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

from models.paper import Paper
from utils.title_normalizer import normalize_title, title_similarity

log = logging.getLogger(__name__)


@dataclass
class MergeStats:
    input_count: int = 0
    output_count: int = 0
    merged_by_doi: int = 0
    merged_by_arxiv: int = 0
    merged_by_title_exact: int = 0
    merged_by_title_fuzzy: int = 0

    @property
    def duplicates_removed(self) -> int:
        return max(0, self.input_count - self.output_count)

    def as_text(self) -> str:
        return (
            f"{self.input_count} 条 → {self.output_count} 条"
            f"（DOI合并 {self.merged_by_doi}，arXiv合并 {self.merged_by_arxiv}，"
            f"标题相同 {self.merged_by_title_exact}，标题近似 {self.merged_by_title_fuzzy}）"
        )


class MergeService:
    """Stateful merger; a new instance is cheap and thread-local."""

    def __init__(self, title_threshold: float = 95.0, fuzzy_scan_limit: int = 4000) -> None:
        self.title_threshold = float(title_threshold)
        self.fuzzy_scan_limit = fuzzy_scan_limit

    # ------------------------------------------------------------------ #
    def merge(self, batches: Iterable[Iterable[Paper]]) -> tuple[list[Paper], MergeStats]:
        stats = MergeStats()
        by_doi: dict[str, int] = {}
        by_arxiv: dict[str, int] = {}
        by_title: dict[str, int] = {}
        merged: list[Paper] = []

        for batch in batches:
            if not batch:
                continue
            for paper in batch:
                stats.input_count += 1
                index = self._find_index(paper, merged, by_doi, by_arxiv, by_title, stats)
                if index is None:
                    merged.append(paper)
                    self._register(paper, len(merged) - 1, by_doi, by_arxiv, by_title)
                else:
                    target = merged[index]
                    old_title_key = normalize_title(target.title)
                    old_doi, old_arxiv = target.doi, target.arxiv_id
                    target.merge_from(paper)
                    # Refresh the indexes for fields that may have appeared.
                    if target.doi and target.doi != old_doi:
                        by_doi[target.doi] = index
                    if target.arxiv_id and target.arxiv_id != old_arxiv:
                        by_arxiv[target.arxiv_id] = index
                    new_title_key = normalize_title(target.title)
                    if new_title_key and new_title_key != old_title_key:
                        by_title.pop(old_title_key, None)
                        by_title[new_title_key] = index

        stats.output_count = len(merged)
        log.info("merge: %s", stats.as_text())
        return merged, stats

    # ------------------------------------------------------------------ #
    def _find_index(
        self,
        paper: Paper,
        merged: list[Paper],
        by_doi: dict[str, int],
        by_arxiv: dict[str, int],
        by_title: dict[str, int],
        stats: MergeStats,
    ) -> Optional[int]:
        if paper.doi and paper.doi in by_doi:
            stats.merged_by_doi += 1
            return by_doi[paper.doi]
        if paper.arxiv_id and paper.arxiv_id in by_arxiv:
            stats.merged_by_arxiv += 1
            return by_arxiv[paper.arxiv_id]

        title_key = normalize_title(paper.title)
        if not title_key:
            return None
        if title_key in by_title:
            stats.merged_by_title_exact += 1
            return by_title[title_key]

        # Fuzzy pass.  Compare only against a candidate block (first token) to
        # keep this linear-ish for a few thousand records, then fall back to a
        # full scan for small result sets.
        candidates = self._candidates_for(title_key, merged, by_title)
        best_index: Optional[int] = None
        best_score = 0.0
        for index in candidates:
            score = title_similarity(paper.title, merged[index].title)
            if score > best_score:
                best_score = score
                best_index = index
        if best_index is not None and best_score >= self.title_threshold:
            stats.merged_by_title_fuzzy += 1
            log.debug("fuzzy merge %.1f: %r ~ %r", best_score, paper.title,
                      merged[best_index].title)
            return best_index
        return None

    def _candidates_for(
        self, title_key: str, merged: list[Paper], by_title: dict[str, int]
    ) -> list[int]:
        first = title_key.split(" ", 1)[0] if title_key else ""
        block = [
            index for key, index in by_title.items()
            if key and key.split(" ", 1)[0] == first
        ]
        if block:
            return block
        if len(merged) <= self.fuzzy_scan_limit:
            return list(range(len(merged)))
        # Large lists without a shared first token: skip the O(n) scan.
        return []

    @staticmethod
    def _register(
        paper: Paper,
        index: int,
        by_doi: dict[str, int],
        by_arxiv: dict[str, int],
        by_title: dict[str, int],
    ) -> None:
        if paper.doi:
            by_doi.setdefault(paper.doi, index)
        if paper.arxiv_id:
            by_arxiv.setdefault(paper.arxiv_id, index)
        key = normalize_title(paper.title)
        if key:
            by_title.setdefault(key, index)


def dedupe_papers(papers: Iterable[Paper], title_threshold: float = 95.0) -> tuple[list[Paper], MergeStats]:
    """Convenience wrapper used by tests and the CLI."""
    merger = MergeService(title_threshold=title_threshold)
    return merger.merge([list(papers)])


def dedupe_candidates(candidates: Iterable) -> list:
    """Remove duplicate PDF URLs while preserving priority order."""
    seen: set[str] = set()
    out = []
    for cand in candidates:
        url = (getattr(cand, "url", "") or "").strip()
        if not url:
            continue
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out
