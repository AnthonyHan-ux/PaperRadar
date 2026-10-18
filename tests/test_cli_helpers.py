"""CLI helper tests (file parsing, best-match selection)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli import _best_match, _existing_audit_keys, _read_titles
from models.paper import Paper


def test_read_titles_txt(tmp_path: Path):
    path = tmp_path / "titles.txt"
    path.write_text(
        "# comment\n\nOptical SAR Change Detection\n  Cloud Removal with Diffusion  \n",
        encoding="utf-8",
    )
    assert _read_titles(path) == [
        "Optical SAR Change Detection",
        "Cloud Removal with Diffusion",
    ]


def test_read_titles_csv(tmp_path: Path):
    path = tmp_path / "titles.csv"
    path.write_text(
        "title,year\nOptical SAR Change Detection,2025\nFog Removal,2024\n",
        encoding="utf-8",
    )
    assert _read_titles(path) == ["Optical SAR Change Detection", "Fog Removal"]


def test_read_titles_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        _read_titles(tmp_path / "nope.txt")


def test_best_match_selects_highest_similarity():
    target = "Optical SAR Change Detection with Robust Fusion"
    candidates = [
        Paper(title="Optical SAR Registration", year=2024),
        Paper(title="Optical SAR Change Detection with Robust Fusions", year=2025),
        Paper(title="Unrelated", year=2023),
    ]
    match, score = _best_match(candidates, target)
    assert match is not None
    assert match.year == 2025
    assert score > 95


def test_best_match_returns_none_below_threshold():
    match, score = _best_match([Paper(title="Mediterranean Diet Study")], "Optical SAR Fusion")
    assert match is None
    assert score < 85


def test_existing_audit_keys(tmp_path: Path):
    path = tmp_path / "audit.csv"
    path.write_text(
        "input_title,status\nPaper One,FOUND\nPaper Two,NO_MATCH\n",
        encoding="utf-8",
    )
    assert _existing_audit_keys(path) == {"paper one", "paper two"}
    assert _existing_audit_keys(tmp_path / "missing.csv") == set()
