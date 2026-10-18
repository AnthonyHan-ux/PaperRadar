"""Windows file-name safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.filename import (
    build_pdf_filename,
    first_author_surname,
    make_unique_path,
    sanitize_component,
    shorten_for_windows,
    words_for_filename,
)


@pytest.mark.parametrize(
    "raw",
    [
        'A/B\\C:D*E?F"G<H>I|J',
        "  trailing spaces   ",
        "ending.with.dots...",
        "line\nbreak",
    ],
)
def test_sanitize_removes_windows_illegal_chars(raw):
    cleaned = sanitize_component(raw)
    for char in '<>:"/\\|?*\n':
        assert char not in cleaned
    assert not cleaned.endswith(" ")
    assert not cleaned.endswith(".")


def test_sanitize_reserved_names():
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("lpt1") == "_lpt1"


def test_sanitize_empty_falls_back():
    assert sanitize_component("", fallback="paper") == "paper"
    assert sanitize_component("///", fallback="paper") == "paper"


def test_sanitize_truncates():
    cleaned = sanitize_component("x" * 500, max_len=60)
    assert len(cleaned) <= 60


@pytest.mark.parametrize(
    "author,expected",
    [
        ("Wei Zhang", "Zhang"),
        ("Zhang, Wei", "Zhang"),
        ("W. Zhang", "Zhang"),
        ("Zhang", "Zhang"),
        ("", ""),
    ],
)
def test_first_author_surname(author, expected):
    assert first_author_surname(author) == expected


def test_build_pdf_filename_structure():
    name = build_pdf_filename(
        2026,
        ["Wei Zhang", "Li Wang"],
        "Beyond Attention: Convolutional Global Context for Remote Sensing Change Detection",
    )
    assert name.startswith("2026_Zhang_")
    assert name.endswith(".pdf")
    assert ":" not in name and "/" not in name


def test_build_pdf_filename_is_length_bounded():
    title = "Very " * 80 + "Long Title"
    name = build_pdf_filename(2025, ["A. Author"], title)
    assert len(name) <= 110


def test_build_pdf_filename_handles_missing_year_and_author():
    name = build_pdf_filename(None, [], "")
    assert name.startswith("nd_anon_")
    assert name.endswith(".pdf")


def test_words_for_filename_drops_stopwords():
    assert words_for_filename("The Role of Fog in Change Detection") == (
        "role_fog_change_detection"
    )


def test_make_unique_path(tmp_path: Path):
    target = tmp_path / "paper.pdf"
    target.write_bytes(b"x")
    unique = make_unique_path(target)
    assert unique.name == "paper_2.pdf"
    unique.write_bytes(b"x")
    assert make_unique_path(target).name == "paper_3.pdf"


def test_shorten_for_windows_truncates_long_names():
    long_name = "a" * 300 + ".pdf"
    base = Path("C:/library/2026/CVPR") / long_name
    shortened = shorten_for_windows(base, max_path=200)
    assert len(str(shortened)) <= 200
