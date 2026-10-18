"""Search orchestration: fan out, merge, filter, score.

Providers are queried in parallel (one bad API cannot stall the rest), results
are merged with :class:`~services.merge_service.MergeService`, filtered by year
and venue, and finally scored by :class:`~services.relevance_service.RelevanceService`.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
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
            try:
                found = provider.search(query)
                return provider, found, time.monotonic() - begin, ""
            except ProviderError as exc:
                return provider, [], time.monotonic() - begin, str(exc)
            except Exception as exc:  # never let one provider kill the search
                log.exception("provider %s crashed", provider.name)
                return provider, [], time.monotonic() - begin, f"{type(exc).__name__}: {exc}"

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

        filtered = self._apply_filters(merged, query)
        if score:
            self.relevance.score_papers(filtered, sort=True)
        else:
            filtered.sort(key=lambda p: (p.year or 0), reverse=True)
        outcome.papers = filtered
        outcome.elapsed = time.monotonic() - started

        outcome.reports.sort(
            key=lambda r: enabled.index(r.provider) if r.provider in enabled else 99
        )
        log.info("search done: %s", outcome.summary_text())
        return outcome

    # ------------------------------------------------------------------ #
    def _apply_filters(self, papers: list[Paper], query: SearchQuery) -> list[Paper]:
        from utils.title_normalizer import normalize_text

        directions = [
            normalize_text(d) for d in (query.extra or {}).get("directions") or [] if d
        ]
        result: list[Paper] = []
        for paper in papers:
            if not paper.title:
                continue
            if query.year_from or query.year_to:
                year = paper.year
                if year is not None:
                    if query.year_from and year < int(query.year_from):
                        continue
                    if query.year_to and year > int(query.year_to):
                        continue
            if query.venues and not venue_matches(paper.venue, query.venues):
                continue
            if directions:
                haystack = normalize_text(
                    " ".join(filter(None, [paper.title, paper.abstract or "",
                                           " ".join(paper.keywords)]))
                )
                if not any(direction and direction in haystack for direction in directions):
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
