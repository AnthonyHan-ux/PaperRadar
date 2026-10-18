"""Title normalisation / similarity tests."""

from __future__ import annotations

import pytest

from utils.title_normalizer import (
    clean_text_block,
    contains_term,
    content_tokens,
    is_same_title,
    normalize_text,
    normalize_title,
    title_similarity,
)


def test_normalize_lowercases_and_strips_punctuation():
    raw = "Beyond Attention: Convolutional Global Context — for Remote Sensing!"
    assert normalize_title(raw) == (
        "beyond attention convolutional global context for remote sensing"
    )


def test_normalize_decodes_html_entities_and_tags():
    raw = "Cloud&lt;b&gt;Removal&lt;/b&gt; &amp; Fog Degradation"
    assert normalize_title(raw) == "cloud removal fog degradation"


def test_identical_titles_after_noise_are_100():
    a = "Optical-SAR Change Detection:\n A Survey"
    b = "Optical SAR change detection: a survey."
    assert title_similarity(a, b) == 100.0
    assert is_same_title(a, b, threshold=95)


def test_word_order_does_not_matter():
    a = "Robust Fusion for Remote Sensing Change Detection"
    b = "Change Detection in Remote Sensing with Robust Fusion"
    assert title_similarity(a, b) > 85


def test_unrelated_titles_score_low():
    a = "Deep Learning for Medical Image Segmentation"
    b = "Optical SAR Change Detection with Transformers"
    assert title_similarity(a, b) < 60


def test_empty_titles_score_zero():
    assert title_similarity("", "something") == 0.0
    assert title_similarity(None, None) == 0.0


def test_contains_term_respects_word_boundaries():
    haystack = normalize_text("A study on sars-cov and optical sar imaging")
    assert contains_term(haystack, "optical sar")
    assert contains_term(haystack, "sar imaging")
    assert contains_term(haystack, "sar") is True
    assert contains_term(haystack, "sarscov") is False


def test_content_tokens_drop_stopwords():
    assert content_tokens("The Role of Fog in Remote Sensing") == [
        "role", "fog", "remote", "sensing",
    ]


def test_clean_text_block_removes_jats():
    raw = "<jats:p>Cloud removal <jats:italic>via</jats:italic> fusion</jats:p>"
    assert clean_text_block(raw) == "Cloud removal via fusion"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Multiple   spaces\tand\nnewlines ", "multiple spaces and newlines"),
        ("Café déjà vu", "cafe deja vu"),
        ("Δ-SAR fusion", "sar fusion"),
    ],
)
def test_normalize_variants(raw, expected):
    assert normalize_text(raw) == expected
