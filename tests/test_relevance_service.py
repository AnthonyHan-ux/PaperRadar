"""Explainable relevance scoring tests."""

from __future__ import annotations

from models.paper import Paper
from services.relevance_service import BAND_HIGH, RelevanceService


def test_title_hits_score_full_title_weight(settings, sample_paper):
    service = RelevanceService(settings)
    result = service.score(sample_paper)
    details = {item["field"]: item["points"] for item in result.details}
    assert details["title"] == 40.0
    assert details["abstract"] == 30.0
    assert details["direction"] == 10.0
    assert details["venue"] == 5.0
    assert result.score == 85.0


def test_score_is_explainable(settings, sample_paper):
    service = RelevanceService(settings)
    result = service.score(sample_paper)
    assert result.details
    explanation = result.explain()
    assert "标题命中" in explanation
    assert "change detection" in explanation


def test_negative_terms_reduce_score(settings, sample_paper):
    service = RelevanceService(settings)
    positive = service.score(sample_paper).score
    sample_paper.abstract = (sample_paper.abstract or "") + " Also used for medical imaging."
    negative = service.score(sample_paper).score
    assert negative < positive
    assert negative == positive - 15.0


def test_keyword_field_contributes_when_title_and_abstract_miss(settings):
    service = RelevanceService(settings)
    paper = Paper(
        title="A Completely Unrelated Title",
        keywords=["change detection", "optical SAR", "fog"],
        year=2025,
    )
    result = service.score(paper)
    details = {item["field"]: item["points"] for item in result.details}
    assert details.get("keywords") == 15.0


def test_no_hits_scores_zero(settings):
    service = RelevanceService(settings)
    paper = Paper(title="Ancient Greek Pottery Chronology", year=2020)
    result = service.score(paper)
    assert result.score == 0.0
    assert result.band == "低相关"


def test_band_thresholds(settings):
    service = RelevanceService(settings)
    assert service.band_for(95) == BAND_HIGH
    assert service.band_for(70) == "较相关"
    assert service.band_for(50) == "可能相关"
    assert service.band_for(10) == "低相关"


def test_score_is_clamped_to_100(settings, sample_paper):
    service = RelevanceService(settings)
    result = service.score(sample_paper)
    assert 0 <= result.score <= 100


def test_score_papers_sorts_descending(settings):
    service = RelevanceService(settings)
    low = Paper(title="Unrelated Topic", year=2024)
    high = Paper(
        title="Optical SAR Change Detection for Building Damage Assessment",
        abstract="Robust fusion under fog, cloud and degradation.",
        venue="CVPR",
        year=2025,
    )
    papers = service.score_papers([low, high])
    assert papers[0] is high
    assert papers[0].relevance_score > papers[1].relevance_score
    assert papers[0].relevance_band
