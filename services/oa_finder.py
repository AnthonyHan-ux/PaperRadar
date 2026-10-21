"""OA (open access) PDF resolution.

Resolution order (cheap and precise first, expensive fallbacks last):

    1. PDF links already attached by the search providers
    2. arXiv (id, or title lookup when the id is unknown)
    3. OpenAlex best_oa_location / locations
    4. Semantic Scholar openAccessPdf
    5. Unpaywall (requires a configured email)
    6. OpenReview (/pdf?id=...)

Later layers (7-11 of the specification: CVF/PMLR pages, author homepages,
GitHub READMEs, institutional repositories, ResearchGate/SSRN) are V3 work and
are *not* approximated by scraping here — the whole point is to stay legal and
predictable.  A paper for which only those layers would succeed is reported as
``NO_PUBLIC_COPY_FOUND`` in V1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from models.paper import OaStatus, PdfCandidate, PdfStatus, Paper
from providers.base import ProviderError, SearchProvider
from services.merge_service import dedupe_candidates
from utils.config import Settings
from utils.paper_identity import title_match_compatible
from utils.title_normalizer import title_similarity

log = logging.getLogger(__name__)

ARXIV_PDF = "https://arxiv.org/pdf/{arxiv_id}"


@dataclass
class OAFinding:
    candidates: list[PdfCandidate] = field(default_factory=list)
    status: PdfStatus = PdfStatus.NOT_CHECKED
    layers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    paywalled_evidence: bool = False

    def describe(self) -> str:
        if self.candidates:
            return f"发现 {len(self.candidates)} 个公开 PDF 候选（{', '.join(self.layers)}）"
        if self.status == PdfStatus.PAYWALLED_ONLY:
            return "仅有付费版本：未发现任何合法公开副本"
        return "未发现合法公开副本"


class OAFinder:
    def __init__(self, settings: Settings, providers: dict[str, SearchProvider]) -> None:
        self.settings = settings
        self.providers = providers
        self._doi_cache: dict[str, list[PdfCandidate]] = {}

    # ------------------------------------------------------------------ #
    def find(self, paper: Paper) -> OAFinding:
        finding = OAFinding()
        candidates: list[PdfCandidate] = []

        # -- layer 1: provider-supplied links --------------------------- #
        existing = [c for c in paper.pdf_candidates if c.url.lower().startswith("http")]
        if existing:
            candidates.extend(existing)
            finding.layers.append("搜索结果")

        # -- layer 2: arXiv --------------------------------------------- #
        arxiv = self._from_arxiv(paper, finding)
        if arxiv:
            candidates.extend(arxiv)
            finding.layers.append("arXiv")

        # -- layers 3-5: DOI resolvers ---------------------------------- #
        doi = paper.doi
        if doi:
            for name, label in (
                ("openalex", "OpenAlex"),
                ("semantic_scholar", "Semantic Scholar"),
                ("unpaywall", "Unpaywall"),
            ):
                found = self._from_doi_provider(name, paper, doi, finding)
                if found:
                    candidates.extend(found)
                    finding.layers.append(label)

        # -- layer 6: OpenReview ---------------------------------------- #
        openreview = self._from_openreview(paper, finding)
        if openreview:
            candidates.extend(openreview)
            finding.layers.append("OpenReview")

        # DOI enrichment may discover an arXiv ID after the arXiv layer ran.
        if paper.arxiv_id:
            url = ARXIV_PDF.format(arxiv_id=paper.arxiv_id)
            if not any(candidate.url == url for candidate in candidates):
                candidates.append(PdfCandidate(url=url, provider="arXiv", is_oa=True))
                if "arXiv" not in finding.layers:
                    finding.layers.append("arXiv")
        unique = dedupe_candidates(candidates)
        finding.candidates = unique

        if unique:
            finding.status = PdfStatus.OPEN_ACCESS_FOUND
            if not paper.pdf_status.has_local_file:
                paper.pdf_status = PdfStatus.OPEN_ACCESS_FOUND
            paper.add_candidates(unique)
            if paper.oa_status == OaStatus.UNKNOWN:
                paper.oa_status = OaStatus.OPEN_ACCESS
        elif finding.paywalled_evidence or paper.oa_status == OaStatus.CLOSED:
            # Only structured evidence (OpenAlex/Unpaywall explicitly closed)
            # is allowed to promote "nothing found" to PAYWALLED_ONLY.
            finding.status = PdfStatus.PAYWALLED_ONLY
            if not paper.pdf_status.has_local_file:
                paper.pdf_status = PdfStatus.PAYWALLED_ONLY
            paper.oa_status = OaStatus.CLOSED
        else:
            # No candidate and no evidence either way: stay UNKNOWN.  Saying
            # "paywalled" here would be a lie we cannot back up.
            finding.status = PdfStatus.NO_PUBLIC_COPY_FOUND
            if not paper.pdf_status.has_local_file:
                paper.pdf_status = PdfStatus.NO_PUBLIC_COPY_FOUND
            if paper.oa_status not in (OaStatus.UNKNOWN,):
                paper.oa_status = OaStatus.UNKNOWN

        log.info(
            "OA resolve: %s | %s | %s",
            finding.status.value, paper.title[:70], ", ".join(finding.layers) or "-",
        )
        return finding

    # ------------------------------------------------------------------ #
    def _from_arxiv(self, paper: Paper, finding: OAFinding) -> list[PdfCandidate]:
        # A known arXiv id needs no network call: the PDF URL is canonical.
        if paper.arxiv_id:
            url = ARXIV_PDF.format(arxiv_id=paper.arxiv_id)
            if url not in paper.pdf_urls:
                return [PdfCandidate(url=url, provider="arXiv", is_oa=True)]
            return []

        provider = self.providers.get("arxiv")
        if provider is None or not provider.available():
            return []
        out: list[PdfCandidate] = []
        if not paper.title or len(paper.title) < 12:
            return []
        try:
            match = provider.get_by_title(paper.title)
        except ProviderError as exc:
            log.debug("arXiv title lookup failed: %s", exc)
            return []
        if match is None:
            return []
        score = title_similarity(paper.title, match.title)
        if score < 90 or not match.arxiv_id or not title_match_compatible(paper, match, fuzzy=True):
            return []
        paper.arxiv_id = paper.arxiv_id or match.arxiv_id
        if not paper.abstract and match.abstract:
            paper.abstract = match.abstract
        url = ARXIV_PDF.format(arxiv_id=match.arxiv_id)
        finding.notes.append(f"arXiv 标题匹配 {score:.0f} 分")
        out.append(PdfCandidate(url=url, provider="arXiv", is_oa=True))
        return out

    def _from_doi_provider(
        self, name: str, paper: Paper, doi: str, finding: OAFinding
    ) -> list[PdfCandidate]:
        provider = self.providers.get(name)
        if provider is None or not provider.available():
            return []
        try:
            match = provider.get_by_doi(doi)
        except (ProviderError, Exception) as exc:  # provider is best-effort
            log.debug("%s DOI lookup failed for %s: %s", name, doi, exc)
            return []
        if match is None:
            return []

        if match.doi and match.doi != doi:
            finding.notes.append(f"{name} 返回了冲突 DOI，未采用该记录")
            return []
        if match.oa_status == OaStatus.CLOSED and name in ("openalex", "unpaywall"):
            finding.paywalled_evidence = True
        if not paper.abstract and match.abstract:
            paper.abstract = match.abstract
        if not paper.venue and match.venue:
            paper.venue = match.venue
        if not paper.arxiv_id and match.arxiv_id:
            paper.arxiv_id = match.arxiv_id

        found = [c for c in match.pdf_candidates if c.url.lower().startswith("http")]
        if found:
            log.debug("%s: %d candidate(s) for %s", name, len(found), doi)
        return found

    def _from_openreview(self, paper: Paper, finding: OAFinding) -> list[PdfCandidate]:
        provider = self.providers.get("openreview")
        if provider is None or not provider.available() or not paper.title:
            return []
        try:
            match = provider.get_by_title(paper.title)
        except (ProviderError, Exception) as exc:
            log.debug("OpenReview lookup failed: %s", exc)
            return []
        if match is None:
            return []
        if title_similarity(paper.title, match.title) < 92 or not title_match_compatible(paper, match, fuzzy=True):
            return []
        if not paper.year and match.year:
            paper.year = match.year
        return [c for c in match.pdf_candidates if c.url.lower().startswith("http")]
