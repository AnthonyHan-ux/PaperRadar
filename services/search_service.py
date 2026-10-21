"""Search orchestration: fan out, merge, filter, score.

Providers are queried in parallel (one bad API cannot stall the rest), results
are merged with :class:`~services.merge_service.MergeService`, filtered by year
and venue, and finally scored by :class:`~services.relevance_service.RelevanceService`.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from typing import Optional

from models.paper import Paper, SearchMode
from providers.arxiv import ArxivProvider
from providers.base import ProviderError, SearchProvider, SearchQuery
from providers.crossref import CrossrefProvider
from providers.http import HttpClient
from providers.openalex import OpenAlexProvider
from providers.openreview import OpenReviewProvider
from providers.semantic_scholar import SemanticScholarProvider
from providers.unpaywall import UnpaywallProvider
from services.merge_service import MergeService, MergeStats
from services.relevance_service import RelevanceService
from utils.config import Settings
from utils.venues import venue_matches
from utils.search_catalog import resolve_term, topic_aliases, search_variants
from utils.search_matching import text_matches
from utils.publication_dates import date_match

log = logging.getLogger(__name__)

PROVIDER_CLASSES: dict[str, type[SearchProvider]] = {
    "openalex": OpenAlexProvider,
    "semantic_scholar": SemanticScholarProvider,
    "crossref": CrossrefProvider,
    "arxiv": ArxivProvider,
    "unpaywall": UnpaywallProvider,
    "openreview": OpenReviewProvider,
}


def build_providers(settings: Settings, http: HttpClient) -> dict[str, SearchProvider]:
    providers: dict[str, SearchProvider] = {}
    for name, cls in PROVIDER_CLASSES.items():
        try:
            providers[name] = cls(settings, http)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("provider %s failed to initialise: %s", name, exc)
    return providers


@dataclass
class ProviderReport:
    provider: str
    display_name: str
    count: int = 0
    elapsed: float = 0.0
    error: str = ""
    skipped: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.error and not self.skipped


@dataclass
class SearchOutcome:
    query: SearchQuery
    papers: list[Paper] = field(default_factory=list)
    reports: list[ProviderReport] = field(default_factory=list)
    stats: MergeStats = field(default_factory=MergeStats)
    elapsed: float = 0.0
    warnings: list[str] = field(default_factory=list)
    filter_counts: dict[str, int] = field(default_factory=dict)

    def feedback_heading(self) -> str:
        attempted = [report for report in self.reports if not report.skipped]
        if not attempted:
            return '未启用可用的检索来源'
        if all(report.error for report in attempted) and not self.stats.input_count:
            return '检索来源全部失败，请查看原因后重试'
        if self.papers:
            return f'检索完成：找到 {len(self.papers)} 篇'
        if self.stats.output_count:
            return f'检索完成：筛选后 0 篇（筛选前 {self.stats.output_count} 篇）'
        return '检索完成：当前条件下未找到文献'

    def feedback_text(self) -> str:
        query = self.query
        original = query.extra.get('original_text')
        shown = f'{original} → {query.text}' if original and original != query.text else (query.text or '仅方向 / 期刊条件')
        lines = ['实际检索词：' + shown]
        if query.extra.get('match_mode') == 'fuzzy':
            variants = search_variants(query.text) if query.text else []
            if len(variants)>1:
                lines.append('同义扩展：' + ' / '.join(variants))
        if re.search(r'[\u4e00-\u9fff]',query.text):
            lines.append('此中文词暂无内置英文对照，当前按原文检索；可改用英文或选择中英建议。')
        if query.venues:
            lines.append('期刊 / 会议限制：' + '、'.join(query.venues))
        if query.extra.get('directions'):
            lines.append('方向限制：' + '、'.join(query.extra['directions']))
        labels = {'missing_title':'缺少标题','date':'日期超出范围','uncertain_date':'日期精度不足',
                  'year':'年份范围','venue':'期刊 / 会议不匹配','query':'关键词不匹配','direction':'研究方向不匹配'}
        if self.filter_counts:
            lines.append('筛选排除（按顺序统计）：' + '；'.join(f'{labels.get(key,key)} {value} 篇' for key,value in self.filter_counts.items()))
        sources = []
        for report in self.reports:
            if report.skipped:
                continue
            detail = f'{report.display_name} {report.count} 条'
            if report.error:
                message = ('请求受限（429），稍后可重试' if '429' in report.error else
                    '请求超时' if 'timeout' in report.error.lower() else
                    '访问受限 / 认证失败' if any(code in report.error for code in ('401','403')) else
                    '请求失败，详见日志')
                detail += '（' + message + '）'
            sources.append(detail)
        lines.append('来源返回：' + ('；'.join(sources) if sources else '无已启用来源'))
        lines.extend(self.warnings)
        if not self.papers and (query.venues or query.extra.get('directions')):
            lines.append('当前关键词、方向、期刊需同时满足；可用下方按钮放宽条件重新检索。')
        return '\n'.join(lines)

    @property
    def total_before_merge(self) -> int:
        return self.stats.input_count

    def errors(self) -> list[tuple[str, str]]:
        return [(r.display_name, r.error) for r in self.reports if r.error]

    def summary_text(self) -> str:
        provider_bits = "，".join(
            f"{r.display_name} {r.count}" for r in self.reports if r.ok
        )
        text = f"合并后 {len(self.papers)} 篇（{self.stats.as_text()}），用时 {self.elapsed:.1f}s"
        if provider_bits:
            text += f"；来源：{provider_bits}"
        if self.errors():
            text += "；失败：" + "，".join(name for name, _ in self.errors())
        if self.warnings:
            text += '；' + '；'.join(self.warnings)
        return text


class SearchService:
    def __init__(
        self,
        settings: Settings,
        http: HttpClient,
        providers: Optional[dict[str, SearchProvider]] = None,
        relevance: Optional[RelevanceService] = None,
    ) -> None:
        self.settings = settings
        self.http = http
        self.providers = providers or build_providers(settings, http)
        self.relevance = relevance or RelevanceService(settings)
        self.merge_service = MergeService(
            title_threshold=float(settings.search_cfg.get("title_dedupe_threshold", 95))
        )

    # ------------------------------------------------------------------ #
    def search(
        self,
        query: SearchQuery,
        score: bool = True,
        progress=None,
    ) -> SearchOutcome:
        started = time.monotonic()
        if query.mode in (SearchMode.KEYWORDS, SearchMode.TOPIC):
            text = resolve_term(query.text) if query.text else ''
            if text != query.text:
                query = replace(query, text=text, extra={**query.extra, 'original_text':query.text})
        outcome = SearchOutcome(query=query)

        enabled = [p for p in self.settings.enabled_providers if p in self.providers]
        # OpenReview/Unpaywall are enrichment providers: only call them when the
        # user explicitly enabled them in config.
        active = [self.providers[name] for name in enabled]
        skipped = [name for name in self.providers if name not in enabled]

        for name in skipped:
            provider = self.providers[name]
            outcome.reports.append(
                ProviderReport(
                    provider=name,
                    display_name=provider.display_name,
                    skipped="未启用",
                )
            )

        if not active:
            log.warning("no providers enabled")
            return outcome

        results: dict[str, list[Paper]] = {}
        provider_timeout = float(
            self.settings.search_cfg.get("provider_timeout_seconds", 40)
        )
        # Hard wall-clock ceiling for the whole fan-out.  A provider that
        # ignores its own HTTP timeouts (or a provider that is simply slow)
        # must never extend this, which is why the executor is *not* used as a
        # context manager: exiting the ``with`` block would call
        # ``shutdown(wait=True)`` and block on the very thread we gave up on.
        budget = max(0.05, provider_timeout * 1.5)
        deadline = time.monotonic() + budget

        def run(provider: SearchProvider):
            begin = time.monotonic()
            routes = [query]
            if query.text and query.mode in (SearchMode.KEYWORDS,SearchMode.TOPIC) and query.extra.get('match_mode') == 'fuzzy':
                routes = [replace(query,text=variant) for variant in search_variants(query.text)]
            directions = query.extra.get('directions') or []
            if not query.text and directions:
                routes = [replace(query, text=resolve_term(term), mode=SearchMode.TOPIC) for term in directions]
            if provider.name == 'crossref' and len(query.venues) > 1:
                routes = [replace(route, venues=[venue]) for route in routes for venue in query.venues]
            if not query.text and not directions and query.venues and provider.name != 'crossref':
                return provider, [], time.monotonic() - begin, ''
            found, errors = [], []
            for route in routes:
                if time.monotonic() >= deadline:
                    errors.append('request_budget_exhausted (partial results retained)')
                    break
                try:
                    found.extend(provider.search(route))
                except ProviderError as exc:
                    errors.append(str(exc))
                except Exception as exc:  # never let one provider kill the search
                    log.exception('provider %s crashed', provider.name)
                    errors.append(f'{type(exc).__name__}: {exc}')
            return provider, found, time.monotonic() - begin, '; '.join(dict.fromkeys(errors))

        executor = ThreadPoolExecutor(
            max_workers=min(6, len(active)), thread_name_prefix="search"
        )
        pending = {executor.submit(run, provider): provider for provider in active}
        try:
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, _ = wait(
                    pending.keys(), timeout=remaining, return_when=FIRST_COMPLETED
                )
                if not done:
                    break
                for future in done:
                    provider = pending.pop(future)
                    try:
                        _provider, found, elapsed, error = future.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        found, elapsed, error = [], 0.0, f"{type(exc).__name__}: {exc}"
                    results[provider.name] = found
                    outcome.reports.append(
                        ProviderReport(
                            provider=provider.name,
                            display_name=provider.display_name,
                            count=len(found),
                            elapsed=elapsed,
                            error=error,
                        )
                    )
        finally:
            # Everything still pending at this point missed the deadline.
            leftovers = list(pending.items())
            for future, _provider in leftovers:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            for _future, provider in leftovers:
                outcome.reports.append(
                    ProviderReport(
                        provider=provider.name,
                        display_name=provider.display_name,
                        error=f"provider_timeout（>{budget:.1f}s）",
                        timed_out=True,
                    )
                )

        # Deterministic provider order for reproducible merges.
        order = [name for name in enabled if name in results]
        batches = [results[name] for name in order]
        merged, stats = self.merge_service.merge(batches)
        outcome.stats = stats

        filtered = self._apply_filters(merged, query, counts=outcome.filter_counts)
        if score:
            self.relevance.score_papers(filtered, sort=True, query=query)
        else:
            filtered.sort(key=lambda p: (p.year or 0), reverse=True)
        outcome.papers = filtered
        if query.date_from or query.date_to:
            uncertain = sum(date_match(p,query) in ('uncertain','unknown') for p in merged)
            if uncertain:
                kept = bool(query.extra.get('include_uncertain_dates'))
                outcome.warnings.append(f'日期不确定 {uncertain} 条（{"保留待核对" if kept else "已排除，可勾选保留"}）')
            for paper in filtered:
                if date_match(paper,query) in ('uncertain','unknown'):
                    paper.relevance_details.append({'note':'日期精度不足：此条尚不能确认落在所选日期内。'})
            if any(p.date_kind == 'submitted' for p in filtered):
                outcome.warnings.append('arXiv 日期为首投稿日期')
        outcome.elapsed = time.monotonic() - started

        outcome.reports.sort(
            key=lambda r: enabled.index(r.provider) if r.provider in enabled else 99
        )
        log.info("search done: %s", outcome.summary_text())
        return outcome

    # ------------------------------------------------------------------ #
    def _apply_filters(self, papers: list[Paper], query: SearchQuery,
                       counts: Optional[dict[str,int]] = None) -> list[Paper]:
        from utils.title_normalizer import normalize_text

        directions = [d for d in (query.extra or {}).get('directions') or [] if d]
        def excluded(reason):
            if counts is not None:
                counts[reason] = counts.get(reason,0)+1
        result: list[Paper] = []
        for paper in papers:
            if not paper.title:
                excluded('missing_title')
                continue
            if query.date_from or query.date_to:
                state = date_match(paper,query)
                if state == 'outside' or (state in ('unknown','uncertain') and not query.extra.get('include_uncertain_dates')):
                    excluded('date' if state == 'outside' else 'uncertain_date')
                    continue
            elif query.year_from or query.year_to:
                year = paper.year
                if year is not None:
                    if query.year_from and year < int(query.year_from):
                        excluded('year')
                        continue
                    if query.year_to and year > int(query.year_to):
                        excluded('year')
                        continue
            if query.venues and not venue_matches(paper.venue, query.venues):
                excluded('venue')
                continue
            match_mode = query.extra.get('match_mode')
            if match_mode in ('fuzzy','exact') and query.text and not query.is_identifier_query:
                text = paper.title if query.mode == SearchMode.TITLE else (
                    ' '.join(paper.authors) if query.mode == SearchMode.AUTHOR else
                    ' '.join([paper.title,paper.abstract or '',*paper.keywords]))
                if not any(text_matches(alias,text,match_mode=='fuzzy') for alias in topic_aliases(query.text)):
                    excluded('query')
                    continue
            if directions:
                haystack = normalize_text(
                    " ".join(filter(None, [paper.title, paper.abstract or "",
                                           " ".join(paper.keywords)]))
                )
                if not any(text_matches(alias,haystack,query.extra.get('match_mode')!='exact')
                           for direction in directions for alias in topic_aliases(direction)):
                    excluded('direction')
                    continue
            result.append(paper)
        return result

    # ------------------------------------------------------------------ #
    def search_profile(self, limit: Optional[int] = None,
                       year_from: Optional[int] = None,
                       year_to: Optional[int] = None) -> SearchOutcome:
        """Search using the saved research profile ("我的方向")."""
        terms = [t for t in self.settings.profile.get("keywords", []) if t][:4]
        text = " ".join(terms) if terms else str(self.settings.profile.get("name") or "")
        query = SearchQuery(
            text=text,
            mode=SearchMode.KEYWORDS,
            year_from=year_from,
            year_to=year_to,
            limit=limit or int(self.settings.search_cfg.get("default_limit", 30)),
        )
        return self.search(query)
