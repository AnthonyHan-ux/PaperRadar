"""OA resolution tests with stub providers (no network)."""

from __future__ import annotations

from models.paper import OaStatus, PdfCandidate, PdfStatus, Paper
from services.oa_finder import OAFinder


class StubProvider:
    def __init__(self, name, by_doi=None, by_title=None, available=True, error=None):
        self.name = name
        self._by_doi = by_doi
        self._by_title = by_title
        self._available = available
        self._error = error

    def available(self) -> bool:
        return self._available

    def get_by_doi(self, doi):
        if self._error:
            raise self._error
        return self._by_doi

    def get_by_title(self, title):
        if self._error:
            raise self._error
        return self._by_title


def _cand(url, provider="test"):
    return [PdfCandidate(url=url, provider=provider)]


def test_arxiv_id_yields_pdf_candidate(settings):
    paper = Paper(title="Optical SAR Change Detection", arxiv_id="2501.01234", doi=None)
    finder = OAFinder(settings, {"arxiv": StubProvider("arxiv")})
    finding = finder.find(paper)
    assert finding.status == PdfStatus.OPEN_ACCESS_FOUND
    assert finding.candidates[0].url == "https://arxiv.org/pdf/2501.01234"
    assert "arXiv" in finding.layers
    assert paper.pdf_status == PdfStatus.OPEN_ACCESS_FOUND


def test_arxiv_title_lookup_sets_id(settings):
    paper = Paper(title="Robust Cloud Removal for Optical Imagery", year=2025)
    match = Paper(
        title="Robust Cloud Removal for Optical Imagery",
        arxiv_id="2502.09999",
        pdf_candidates=_cand("https://arxiv.org/pdf/2502.09999"),
    )
    finder = OAFinder(settings, {"arxiv": StubProvider("arxiv", by_title=match)})
    finding = finder.find(paper)
    assert paper.arxiv_id == "2502.09999"
    assert finding.candidates


def test_doi_resolvers_are_deduplicated(settings):
    paper = Paper(
        title="Building Damage Assessment with Multimodal Fusion",
        doi="10.1016/j.rse.2025.0001",
    )
    openalex = Paper(
        title=paper.title,
        pdf_candidates=_cand("https://example.org/paper.pdf", "OpenAlex"),
    )
    s2 = Paper(
        title=paper.title,
        pdf_candidates=_cand("https://example.org/paper.pdf", "Semantic Scholar"),
    )
    unpaywall = Paper(
        title=paper.title,
        pdf_candidates=_cand("https://repo.example.org/paper.pdf", "Unpaywall"),
    )
    providers = {
        "openalex": StubProvider("openalex", by_doi=openalex),
        "semantic_scholar": StubProvider("semantic_scholar", by_doi=s2),
        "unpaywall": StubProvider("unpaywall", by_doi=unpaywall),
    }
    finding = OAFinder(settings, providers).find(paper)
    urls = [c.url for c in finding.candidates]
    assert len(urls) == 2
    assert "https://example.org/paper.pdf" in urls
    assert "https://repo.example.org/paper.pdf" in urls


def test_paywalled_only_when_provider_reports_closed(settings):
    paper = Paper(title="Closed Access Study", doi="10.1000/closed")
    closed = Paper(title=paper.title, oa_status=OaStatus.CLOSED)
    finder = OAFinder(settings, {"openalex": StubProvider("openalex", by_doi=closed)})
    finding = finder.find(paper)
    assert finding.status == PdfStatus.PAYWALLED_ONLY
    assert paper.pdf_status == PdfStatus.PAYWALLED_ONLY
    assert paper.oa_status == OaStatus.CLOSED


def test_no_public_copy_found(settings):
    paper = Paper(title="Obscure Paper Without Indexing", doi="10.1000/none")
    finder = OAFinder(settings, {})
    finding = finder.find(paper)
    assert finding.status == PdfStatus.NO_PUBLIC_COPY_FOUND


def test_existing_candidates_are_reused(settings, sample_paper):
    finder = OAFinder(settings, {})
    finding = finder.find(sample_paper)
    assert finding.candidates
    assert finding.status == PdfStatus.OPEN_ACCESS_FOUND
    assert any("https://arxiv.org/pdf/2601.01234" == c.url for c in finding.candidates)


def test_provider_errors_do_not_break_resolution(settings):
    from providers.base import NetworkError

    paper = Paper(title="Something With Errors", doi="10.1000/err", arxiv_id="2503.00001")
    finder = OAFinder(
        settings,
        {"openalex": StubProvider("openalex", error=NetworkError("boom"))},
    )
    finding = finder.find(paper)
    # arXiv id still resolves despite OpenAlex failing.
    assert finding.status == PdfStatus.OPEN_ACCESS_FOUND


def test_unavailable_provider_is_skipped(settings):
    paper = Paper(title="No Email Configured", doi="10.1000/x")
    finder = OAFinder(
        settings,
        {"unpaywall": StubProvider("unpaywall", available=False,
                                   by_doi=Paper(title=paper.title,
                                                pdf_candidates=_cand("https://x/y.pdf")))},
    )
    finding = finder.find(paper)
    assert finding.status == PdfStatus.NO_PUBLIC_COPY_FOUND
