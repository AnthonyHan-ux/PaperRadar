"""CLI-level tests: version, download-log CSV export, cache clear."""

from __future__ import annotations

import pytest

from cli import main
from database.db import Database
from models.paper import Paper


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "PaperRadar 1.3.1" in capsys.readouterr().out


def test_stats_exports_download_log_csv(tmp_path, capsys):
    home = tmp_path / "home"
    db = Database(home / "database" / "paperradar.db")
    paper = Paper(title="Logged Paper", doi="10.1000/log", year=2025)
    paper_id = db.insert(paper)
    paper.id = paper_id
    db.log_download(
        paper, status="VALIDATED", url="https://example.org/paper.pdf",
        provider="arXiv", reason="ok", http_status=200,
    )
    db.log_download(
        paper, status="PUBLIC_BUT_BLOCKED", url="https://example.org/blocked.pdf",
        provider="OpenAlex", reason="403", http_status=403,
    )
    db.close_all()

    csv_path = tmp_path / "downloads.csv"
    code = main(["--home", str(home), "stats", "--downloads-csv", str(csv_path)])

    assert code == 0
    text = csv_path.read_text(encoding="utf-8-sig")
    header = text.splitlines()[0]
    for column in ("title", "doi", "url", "provider", "status", "reason",
                   "http_status", "timestamp", "local_path"):
        assert column in header
    assert "https://example.org/paper.pdf" in text
    assert "PUBLIC_BUT_BLOCKED" in text
    assert "403" in text
    assert "10.1000/log" in text


def test_cache_clear_only_removes_cache(tmp_path, capsys):
    home = tmp_path / "home"
    cache_file = home / "cache" / "x.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("{}", encoding="utf-8")
    keep = home / "library" / "keep.pdf"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_bytes(b"%PDF-1.4")

    code = main(["--home", str(home), "cache", "clear"])

    assert code == 0
    assert not cache_file.exists()
    assert keep.exists()
    assert (home / "database" / "paperradar.db").exists()


def test_unknown_command_returns_usage():
    code = main([])
    assert code == 2
