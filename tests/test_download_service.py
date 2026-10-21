"""Download pipeline tests against a local HTTP server (no internet needed)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF is required")

from models.paper import PdfCandidate, PdfStatus, Paper  # noqa: E402
from services.download_service import DownloadService  # noqa: E402

TITLE = "Beyond Attention: Convolutional Global Context for Remote Sensing Change Detection"


def _pdf_bytes(text: str, lines: int = 40) -> bytes:
    document = fitz.open()
    page = document.new_page()
    body = "\n".join([text, "Wei Zhang"] + [f"Line {i} of filler text for the validation layer." for i in range(lines)])
    page.insert_textbox(fitz.Rect(40, 40, 555, 800), body, fontsize=10)
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

    def log_message(self, *args):  # silence test output
        pass


@pytest.fixture()
def server():
    good = _pdf_bytes(TITLE)
    wrong = _pdf_bytes("A Randomized Trial of Mediterranean Diet and Cardiovascular Outcomes")
    _Handler.payloads = {
        "/good.pdf": (200, "application/pdf", good),
        "/wrong.pdf": (200, "application/pdf", wrong),
        "/html": (200, "text/html", b"<html><body>Please log in</body></html>"),
        "/forbidden": (403, "text/html", b"<html>Forbidden</html>"),
        "/payment": (402, "text/html", b"<html>Payment required</html>"),
        "/legal": (451, "text/html", b"<html>Unavailable for legal reasons</html>"),
        "/broken": (500, "text/plain", b"server error"),
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


@pytest.fixture()
def downloader(settings):
    settings.config["download"]["min_pdf_bytes"] = 500
    settings.config["download"]["request_timeout_seconds"] = 20
    settings.config["api"]["max_retries"] = 0  # keep failure tests fast
    service = DownloadService(settings)
    yield service
    service.close()


def _paper(title: str, url: str) -> Paper:
    return Paper(
        title=title,
        authors=["Wei Zhang"],
        year=2026,
        venue="CVPR",
        pdf_candidates=[PdfCandidate(url=url, provider="test")],
    )


def test_successful_download_is_validated_and_moved(server, downloader, settings):
    paper = _paper(TITLE, f"{server}/good.pdf")
    events: list[tuple[str, int, int]] = []
    result = downloader.download(paper, progress=lambda t, d, tot: events.append((t, d, tot)))

    assert result.status == PdfStatus.VALIDATED, result.message
    assert result.path is not None and Path(result.path).exists()
    assert str(result.path).startswith(str(settings.paths.library_dir))
    assert Path(result.path).stat().st_size > 500
    assert result.used_url.endswith("/good.pdf")
    assert events, "progress callback should fire"
    assert paper.local_pdf_path == str(result.path)
    # No stale .part files left behind.
    assert not list(settings.paths.tmp_dir.glob("*.part"))


def test_html_page_is_not_saved(server, downloader, settings):
    paper = _paper(TITLE, f"{server}/html")
    result = downloader.download(paper)
    assert result.status in (PdfStatus.DOWNLOAD_FAILED, PdfStatus.INVALID_PDF)
    assert not list(settings.paths.tmp_dir.glob("*.part"))
    assert not list(settings.paths.library_dir.rglob("*.pdf"))


def test_forbidden_becomes_public_but_blocked(server, downloader):
    paper = _paper(TITLE, f"{server}/forbidden")
    result = downloader.download(paper)
    assert result.status == PdfStatus.PUBLIC_BUT_BLOCKED
    assert result.attempts and result.attempts[0].http_status == 403


def test_wrong_paper_is_detected(server, downloader):
    paper = _paper(TITLE, f"{server}/wrong.pdf")
    result = downloader.download(paper)
    assert result.status == PdfStatus.WRONG_PAPER
    assert not list(Path(downloader.library.root).rglob("*.pdf"))


def test_missing_url_fails_cleanly(server, downloader):
    paper = _paper(TITLE, f"{server}/nope.pdf")
    result = downloader.download(paper)
    assert result.status == PdfStatus.DOWNLOAD_FAILED
    assert result.attempts[0].http_status == 404


def test_falls_back_to_next_candidate(server, downloader):
    paper = Paper(
        title=TITLE,
        authors=["Wei Zhang"],
        year=2026,
        venue="CVPR",
        pdf_candidates=[
            PdfCandidate(url=f"{server}/forbidden", provider="first"),
            PdfCandidate(url=f"{server}/good.pdf", provider="second"),
        ],
    )
    result = downloader.download(paper)
    assert result.status == PdfStatus.VALIDATED
    assert len(result.attempts) == 2
    assert result.attempts[0].http_status == 403


def test_no_candidates_is_reported(downloader):
    paper = Paper(title=TITLE)
    result = downloader.download(paper)
    assert result.status == PdfStatus.NO_PUBLIC_COPY_FOUND


def test_cancellation_stops_download(server, downloader):
    event = threading.Event()
    event.set()
    paper = _paper(TITLE, f"{server}/good.pdf")
    result = downloader.download(paper, cancel_event=event)
    assert result.status == PdfStatus.DOWNLOAD_FAILED
    assert "取消" in result.message


def test_payment_required_is_paywalled(server, downloader):
    paper = _paper(TITLE, f"{server}/payment")
    result = downloader.download(paper)
    assert result.status == PdfStatus.PAYWALLED_ONLY
    assert result.attempts[0].http_status == 402


def test_legal_restriction_is_paywalled(server, downloader):
    paper = _paper(TITLE, f"{server}/legal")
    result = downloader.download(paper)
    assert result.status == PdfStatus.PAYWALLED_ONLY
    assert result.attempts[0].http_status == 451


def test_server_error_is_not_blocked(server, downloader):
    paper = _paper(TITLE, f"{server}/broken")
    result = downloader.download(paper)
    assert result.status == PdfStatus.DOWNLOAD_FAILED
    assert result.status != PdfStatus.PUBLIC_BUT_BLOCKED
    assert result.attempts[0].http_status == 500


def test_network_error_is_not_blocked(downloader):
    # Port 1 on localhost is closed.  Depending on the proxy configuration this
    # surfaces as a connection error or a 502 from the proxy, but it must never
    # be classified as "public but blocked".
    paper = _paper(TITLE, "http://127.0.0.1:1/paper.pdf")
    result = downloader.download(paper)
    assert result.status == PdfStatus.DOWNLOAD_FAILED
    assert result.status != PdfStatus.PUBLIC_BUT_BLOCKED
    status = result.attempts[0].http_status
    assert status is None or status >= 500
