"""End-to-end AppService workflow tests (fake providers + local PDF server).

Covers: search -> merge -> relevance -> OA resolution -> download -> validation
-> database, without touching the network.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF is required")

from models.paper import OaStatus, PdfCandidate, PdfStatus, Paper  # noqa: E402
from providers.base import SearchProvider, SearchQuery  # noqa: E402
from services.app_service import AppService  # noqa: E402

TITLE = "Optical SAR Change Detection with Robust Cross-Event Fusion"


def _pdf_bytes(title: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    body = "\n".join(
        [title, "Wei Zhang, Li Wang", "Abstract: robust fusion under fog and cloud."]
        + [f"Filler line {i} for the validation layer." for i in range(30)]
    )
    page.insert_textbox(fitz.Rect(40, 40, 555, 800), body, fontsize=11)
    data = document.tobytes()
    document.close()
    return data


class _Handler(BaseHTTPRequestHandler):
    payloads: dict[str, tuple[int, str, bytes]] = {}

    def do_GET(self):  # noqa: N802
        status, content_type, body = self.payloads.get(
            self.path, (404, "text/plain", b"not found")
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def pdf_server():
    good = _pdf_bytes(TITLE)
    wrong = _pdf_bytes("A Randomized Trial of Mediterranean Diet in Elderly Patients")
    _Handler.payloads = {
        "/good.pdf": (200, "application/pdf", good),
        "/wrong.pdf": (200, "application/pdf", wrong),
        "/forbidden": (403, "text/html", b"<html>Forbidden</html>"),
    }
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()


class FakeProvider(SearchProvider):
    def __init__(self, name: str, papers=None, by_doi=None):
        self.name = name
        self.display_name = name.upper()
        self._papers = list(papers or [])
        self._by_doi = {str(k).lower(): v for k, v in (by_doi or {}).items()}

    def search(self, query: SearchQuery) -> list[Paper]:
        return [p.copy() for p in self._papers]

    def get_by_doi(self, doi: str):
        paper = self._by_doi.get(str(doi).lower())
        return paper.copy() if paper is not None else None


@pytest.fixture()
def service(settings, pdf_server):
    settings.config["search"]["providers"] = ["openalex", "crossref"]
    settings.config["download"]["min_pdf_bytes"] = 500
    app = AppService(settings=settings)
    app.providers.clear()
    app.providers.update({
        "openalex": FakeProvider("openalex"),
        "crossref": FakeProvider("crossref"),
    })
    try:
        yield app
    finally:
        app.close()


def _paper(url: str | None = None, **kwargs) -> Paper:
    defaults = dict(
        title=TITLE,
        authors=["Wei Zhang"],
        year=2026,
        venue="IEEE TGRS",
        doi="10.1109/TGRS.2026.123456",
        abstract="Optical SAR change detection under fog and cloud degradation.",
        keywords=["change detection"],
        sources=["openalex"],
    )
    if url:
        defaults["pdf_candidates"] = [PdfCandidate(url=url, provider="openalex")]
    defaults.update(kwargs)
    return Paper(**defaults)


def test_search_download_validate_and_persist(service, pdf_server):
    paper = _paper(url=f"{pdf_server}/good.pdf")
    service.providers["openalex"] = FakeProvider("openalex", [paper])

    outcome = service.run_search(SearchQuery(text="optical SAR", limit=5))
    assert len(outcome.papers) == 1
    assert outcome.papers[0].relevance_score is not None

    result = service.download_one(outcome.papers[0])
    assert result.status == PdfStatus.VALIDATED, result.message
    assert result.path is not None and Path(result.path).exists()

    stats = service.stats()
    assert stats.total == 1
    assert stats.with_pdf == 1
    assert stats.validated == 1

    # A second search recognises the local copy.
    again = service.run_search(SearchQuery(text="optical SAR", limit=5))
    assert again.papers[0].local_pdf_path == str(result.path)
    assert again.papers[0].pdf_status == PdfStatus.VALIDATED


def test_duplicate_across_providers_is_merged_once(service):
    a = _paper()
    b = _paper(title=TITLE, abstract="different abstract from crossref " * 3,
               sources=["crossref"])
    service.providers["openalex"] = FakeProvider("openalex", [a])
    service.providers["crossref"] = FakeProvider("crossref", [b])

    outcome = service.run_search(SearchQuery(text="optical SAR", limit=5))
    assert len(outcome.papers) == 1
    assert outcome.stats.duplicates_removed == 1
    service.add_many(outcome.papers)
    assert service.stats().total == 1


def test_blocked_download_is_persisted_as_blocked(service, pdf_server):
    paper = _paper(url=f"{pdf_server}/forbidden")
    service.providers["openalex"] = FakeProvider("openalex", [paper])
    outcome = service.run_search(SearchQuery(text="optical SAR", limit=5))

    result = service.download_one(outcome.papers[0])
    assert result.status == PdfStatus.PUBLIC_BUT_BLOCKED
    assert service.stats().blocked == 1
    assert service.stats().with_pdf == 0


def test_wrong_pdf_is_rejected_and_not_stored(service, pdf_server):
    paper = _paper(url=f"{pdf_server}/wrong.pdf")
    service.providers["openalex"] = FakeProvider("openalex", [paper])
    outcome = service.run_search(SearchQuery(text="optical SAR", limit=5))

    result = service.download_one(outcome.papers[0])
    assert result.status == PdfStatus.WRONG_PAPER
    assert not list(service.library.root.rglob("*.pdf"))
    assert service.stats().with_pdf == 0


def test_no_public_copy_keeps_unknown_oa_state(service):
    paper = _paper()
    service.providers["openalex"] = FakeProvider("openalex", [paper])
    outcome = service.run_search(SearchQuery(text="optical SAR", limit=5))

    result = service.download_one(outcome.papers[0])
    assert result.status == PdfStatus.NO_PUBLIC_COPY_FOUND
    assert outcome.papers[0].oa_status == OaStatus.UNKNOWN
    stats = service.stats()
    assert stats.no_public_copy == 1
    assert stats.paywalled == 0


def test_explicit_closed_evidence_becomes_paywalled(service):
    closed = _paper(oa_status=OaStatus.CLOSED)
    service.providers["openalex"] = FakeProvider(
        "openalex", by_doi={"10.1109/TGRS.2026.123456": closed}
    )
    paper = _paper()
    finding = service.oa_finder.find(paper)
    assert finding.status == PdfStatus.PAYWALLED_ONLY
    assert paper.oa_status == OaStatus.CLOSED


def test_cancelled_download_leaves_no_partial_file(service, pdf_server):
    paper = _paper(url=f"{pdf_server}/good.pdf")
    service.providers["openalex"] = FakeProvider("openalex", [paper])
    outcome = service.run_search(SearchQuery(text="optical SAR", limit=5))

    event = threading.Event()
    event.set()
    result = service.download_one(outcome.papers[0], cancel_event=event)

    assert result.status == PdfStatus.DOWNLOAD_FAILED
    assert not list(service.settings.paths.tmp_dir.glob("*.part"))
    assert not list(service.library.root.rglob("*.pdf"))


def test_missing_local_file_is_marked_not_deleted(service):
    paper = _paper(doi="10.1109/TGRS.2026.654321")
    paper_id, _ = service.add_to_library(paper)
    service.db.update_download_state(
        paper_id, PdfStatus.VALIDATED, "C:/nowhere/missing.pdf"
    )

    service.providers["openalex"] = FakeProvider("openalex", [paper])
    outcome = service.run_search(SearchQuery(text="optical SAR", limit=5))

    assert outcome.papers[0].pdf_status == PdfStatus.MISSING_LOCAL_FILE
    assert service.stats().total == 1  # record kept
    stored = service.db.get(paper_id)
    assert stored is not None
    assert stored.pdf_status == PdfStatus.MISSING_LOCAL_FILE


def test_export_after_workflow(service, pdf_server, tmp_path):
    paper = _paper(url=f"{pdf_server}/good.pdf")
    service.providers["openalex"] = FakeProvider("openalex", [paper])
    outcome = service.run_search(SearchQuery(text="optical SAR", limit=5))
    service.download_one(outcome.papers[0])

    target = tmp_path / "export.csv"
    service.export_papers(service.library_papers(only_local=True), "csv", target)
    text = target.read_text(encoding="utf-8-sig")
    assert "Optical SAR Change Detection" in text
    assert "VALIDATED" in text
