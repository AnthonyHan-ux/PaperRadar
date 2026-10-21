"""Explainable relevance scoring.

The score is a transparent weighted sum over five evidence channels, each of
which saturates (two title hits are already "enough", ten add nothing).  Every
point is traceable: the GUI shows a tooltip listing exactly which term hit which
field and how many points it contributed.

    title      40   term found in the title
    abstract   30   term found in the abstract
    keywords   15   term found in provider keywords
    direction  10   a research direction matches
    venue       5   priority venue
    negative   -15 per unrelated term, capped

A term counts only once, in its strongest field (title > keywords > abstract),
so a paper cannot farm points by repeating one word.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from models.paper import Paper, SearchMode, clean_doi, clean_arxiv_id
from providers.base import SearchQuery
from utils.config import Settings
from utils.title_normalizer import (
    contains_term,
    normalize_text,
    title_similarity,
    unique_preserving_order,
    content_tokens,
)
from utils.venues import canonical_venue

log = logging.getLogger(__name__)

BAND_HIGH = "高度相关"
BAND_MEDIUM = "较相关"
BAND_LOW = "可能相关"
BAND_NONE = "低相关"


@dataclass
class RelevanceResult:
    score: float = 0.0
    band: str = BAND_NONE
    details: list[dict[str, Any]] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)

    def explain(self) -> str:
        if not self.details and not self.negative_terms:
            return "未命中任何研究方向关键词（0 分）"
        lines = []
        for item in self.details:
            if 'note' in item:
                lines.append(item['note'])
                continue
            terms = "、".join(item.get("terms", [])[:6])
            lines.append(f"{item['label']}：{terms} → +{item['points']:.0f}")
        if self.negative_terms:
            lines.append(f"排除词命中：{'、'.join(self.negative_terms)} → 已扣分")
        return "\n".join(lines)


class RelevanceService:
    """Scores papers against the configured research profile."""

    def __init__(self, settings: Settings) -> None:
        cfg = settings.scoring_cfg or {}
        weights = cfg.get("weights") or {}
        self.weights = {
            "title": float(weights.get("title", 40)),
            "abstract": float(weights.get("abstract", 30)),
            "keywords": float(weights.get("keywords", 15)),
            "direction": float(weights.get("direction", 10)),
            "venue": float(weights.get("venue", 5)),
        }
        saturation = cfg.get("saturation") or {}
        self.saturation = {
            "title": max(1.0, float(saturation.get("title", 2))),
            "abstract": max(1.0, float(saturation.get("abstract", 4))),
            "keywords": max(1.0, float(saturation.get("keywords", 3))),
            "direction": max(1.0, float(saturation.get("direction", 1))),
        }
        self.negative_penalty = float(cfg.get("negative_penalty", 15))
        self.max_negative_penalty = float(cfg.get("max_negative_penalty", 30))
        bands = cfg.get("bands") or {}
        self.band_high = float(bands.get("high", 90))
        self.band_medium = float(bands.get("medium", 70))
        self.band_low = float(bands.get("low", 50))

        self.terms = unique_preserving_order(settings.profile_terms())
        self.negative_terms = unique_preserving_order(settings.profile_negative_terms())
        self.priority_venues = unique_preserving_order(settings.profile_venues())
        self.directions = unique_preserving_order(settings.directions)
        self.profile_name = str(settings.profile.get("name") or "我的方向")

    # ------------------------------------------------------------------ #
    def band_for(self, score: float) -> str:
        if score >= self.band_high:
            return BAND_HIGH
        if score >= self.band_medium:
            return BAND_MEDIUM
        if score >= self.band_low:
            return BAND_LOW
        return BAND_NONE

    def score(self, paper: Paper) -> RelevanceResult:
        title_norm = normalize_text(paper.title)
        abstract_norm = normalize_text(paper.abstract)
        keywords_norm = normalize_text(" ".join(paper.keywords))
        venue_norm = normalize_text(paper.venue)

        title_hits: list[str] = []
        keyword_hits: list[str] = []
        abstract_hits: list[str] = []

        for term in self.terms:
            if contains_term(title_norm, term):
                title_hits.append(term)
            elif contains_term(abstract_norm, term):
                abstract_hits.append(term)
            elif contains_term(keywords_norm, term):
                keyword_hits.append(term)

        direction_hits = [
            direction
            for direction in self.directions
            if contains_term(title_norm, direction)
            or contains_term(abstract_norm, direction)
            or contains_term(keywords_norm, direction)
        ]

        venue_hit = self._venue_hit(paper.venue, venue_norm)
        negative_hits = [
            term
            for term in self.negative_terms
            if contains_term(title_norm, term) or contains_term(abstract_norm, term)
        ]

        details: list[dict[str, Any]] = []
        total = 0.0

        def add(field_name: str, label: str, hits: list[str], saturation_key: str) -> None:
            nonlocal total
            if not hits:
                return
            fraction = min(1.0, len(hits) / self.saturation[saturation_key])
            points = self.weights[field_name] * fraction
            total += points
            details.append(
                {
                    "field": field_name,
                    "label": label,
                    "terms": hits,
                    "points": round(points, 1),
                    "count": len(hits),
                }
            )

        add("title", "标题命中", title_hits, "title")
        add("keywords", "关键词命中", keyword_hits, "keywords")
        add("abstract", "摘要命中", abstract_hits, "abstract")
        add("direction", "研究方向匹配", direction_hits, "direction")
        if venue_hit:
            total += self.weights["venue"]
            details.append(
                {
                    "field": "venue",
                    "label": "优先期刊/会议",
                    "terms": [venue_hit],
                    "points": round(self.weights["venue"], 1),
                    "count": 1,
                }
            )

        penalty = min(self.max_negative_penalty, self.negative_penalty * len(negative_hits))
        if penalty:
            total -= penalty
            details.append(
                {
                    "field": "negative",
                    "label": "排除词命中",
                    "terms": negative_hits,
                    "points": -round(penalty, 1),
                    "count": len(negative_hits),
                }
            )

        score = max(0.0, min(100.0, round(total, 1)))
        result = RelevanceResult(
            score=score,
            band=self.band_for(score),
            details=details,
            matched_terms=title_hits + keyword_hits + abstract_hits + direction_hits,
            negative_terms=negative_hits,
        )
        return result

    def _venue_hit(self, venue: Optional[str], venue_norm: str) -> Optional[str]:
        if not venue or not self.priority_venues:
            return None
        canonical = canonical_venue(venue)
        for priority in self.priority_venues:
            priority_norm = normalize_text(priority)
            if not priority_norm:
                continue
            if priority_norm in venue_norm:
                return priority
            if title_similarity(priority, venue) >= 85:
                return priority
            if canonical and canonical != "Other" and canonical in priority:
                return priority
        return None

    # ------------------------------------------------------------------ #
    def score_query(self, paper: Paper, query: SearchQuery) -> RelevanceResult:
        """Lexical query fit, independent of the user's saved subject area."""
        from utils.search_matching import text_matches
        details = []
        if not query.text and query.extra.get('directions'):
            from dataclasses import replace
            alternatives = [self.score_query(paper,replace(query,text=term))
                            for term in query.extra['directions']]
            result = max(alternatives,key=lambda item:item.score)
            result.details.append({'note':'按所选研究方向中匹配最高的一项评分。'})
            return result
        if query.mode == SearchMode.DOI:
            score = 100.0 if clean_doi(query.text) and clean_doi(query.text) == paper.doi else 0.0
            details.append({'field':'identifier', 'label':'DOI 精确匹配', 'terms':[query.text], 'points':score})
        elif query.mode == SearchMode.ARXIV_ID:
            score = 100.0 if clean_arxiv_id(query.text) and clean_arxiv_id(query.text) == paper.arxiv_id else 0.0
            details.append({'field':'identifier', 'label':'arXiv 精确匹配', 'terms':[query.text], 'points':score})
        elif query.mode == SearchMode.TITLE:
            score = title_similarity(query.text, paper.title)
            details.append({'field':'query_title', 'label':'查询标题相似度', 'terms':[query.text], 'points':score})
        else:
            terms = unique_preserving_order([term for term in content_tokens(query.text)
                                             if term not in {'and', 'or', 'not'}])
            channels = [('authors', '查询词（作者）', ' '.join(paper.authors), 100)] if query.mode == SearchMode.AUTHOR else [
                ('title', '查询词（标题）', paper.title, 70),
                ('abstract', '查询词（摘要）', paper.abstract, 20),
                ('keywords', '查询词（关键词）', ' '.join(paper.keywords), 10)]
            score = 0.0
            for field_name, label, text, weight in channels:
                hits = [term for term in terms if contains_term(normalize_text(text), term)
                        or (query.extra.get('match_mode') == 'fuzzy' and text_matches(term,text))]
                points = weight * len(hits) / max(1, len(terms))
                score += points
                if hits:
                    details.append({'field':field_name, 'label':label, 'terms':hits, 'points':round(points,1)})
        if not paper.abstract:
            details.append({'note':'摘要缺失：证据不足，不能据此判断研究结论或论文质量。'})
        if query.extra.get('match_mode') == 'fuzzy':
            details.append({'note':'模糊检索：接受词项片段与轻微拼写差异；召回仍受来源索引覆盖限制。'})
        details.append({'note':'分数仅表示元数据词项匹配，不是论文质量或相关概率。'})
        return RelevanceResult(score=round(score,1), band=self.band_for(score), details=details)

    def score_papers(self, papers: list[Paper], sort: bool = True,
                     query: Optional[SearchQuery] = None) -> list[Paper]:
        for paper in papers:
            if query is None:
                self.apply(paper)
                continue
            profile_result = self.score(paper)
            query_result = self.score_query(paper, query)
            profile_sort = query.extra.get('sort_by') == 'profile'
            result = profile_result if profile_sort else query_result
            paper.relevance_score, paper.relevance_band = result.score, result.band
            paper.relevance_details = list(result.details)
            label, reference = ('查询匹配', query_result) if profile_sort else ('个人方向', profile_result)
            paper.relevance_details.append({'note':f'{label}参考分：{reference.score:.1f}/100；不参与当前排序。'})
            basis = {'profile':'个人研究方向', 'recent':'发表年份'}.get(query.extra.get('sort_by'), '本次查询')
            paper.relevance_details.append({'note':'排序依据：' + basis})
        if sort:
            papers.sort(
                key=lambda p: (
                    (p.publication_date or str(p.year or '')) if query and query.extra.get('sort_by') == 'recent' else '',
                    p.relevance_score if p.relevance_score is not None else -1,
                    p.year or 0,
                ),
                reverse=True,
            )
        return papers

    def apply(self, paper: Paper) -> Paper:
        result = self.score(paper)
        paper.relevance_score = result.score
        paper.relevance_band = result.band
        paper.relevance_details = result.details
        return paper
