"""Venue canonicalisation and config-list union tests."""

from __future__ import annotations

import pytest

from utils.config import DEFAULT_CONFIG, load_settings
from utils.venues import canonical_venue, venue_matches, venue_slug


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("IEEE Transactions on Geoscience and Remote Sensing", "IEEE TGRS"),
        ("TGRS", "IEEE TGRS"),
        ("IEEE Journal of Selected Topics in Applied Earth Observations and Remote Sensing", "IEEE JSTARS"),
        ("IEEE Geoscience and Remote Sensing Letters", "IEEE GRSL"),
        ("ISPRS Journal of Photogrammetry and Remote Sensing", "ISPRS JPRS"),
        ("Remote Sensing of Environment", "Remote Sensing of Environment"),
        ("Remote Sensing", "Remote Sensing (MDPI)"),
        ("Remote Sens.", "Remote Sensing (MDPI)"),
        ("International Journal of Applied Earth Observation and Geoinformation", "JAG"),
        ("Photogrammetric Engineering & Remote Sensing", "Photogrammetric Engineering & Remote Sensing"),
        ("Information Fusion", "Information Fusion"),
        ("CVPR", "CVPR"),
        ("Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition", "CVPR"),
        ("arXiv preprint", "arXiv"),
        ("CoRR", "arXiv"),
    ],
)
def test_canonical_venue(raw, expected):
    assert canonical_venue(raw) == expected


def test_remote_sensing_does_not_shadow_rse():
    assert canonical_venue("Remote Sensing of Environment") == "Remote Sensing of Environment"
    assert canonical_venue("Remote Sensing of Environment") != "Remote Sensing (MDPI)"
    assert canonical_venue("ISPRS Journal of Photogrammetry and Remote Sensing") == "ISPRS JPRS"


def test_venue_slugs_are_folder_safe():
    assert venue_slug("IEEE Transactions on Geoscience and Remote Sensing") == "TGRS"
    assert venue_slug("Remote Sensing") == "Remote_Sensing_MDPI"
    assert venue_slug("IEEE Geoscience and Remote Sensing Letters") == "GRSL"
    for venue in DEFAULT_CONFIG["venues"]:
        slug = venue_slug(venue)
        assert slug and all(ch.isalnum() or ch in "_-" for ch in slug)


def test_unknown_venue_falls_back_to_other():
    assert canonical_venue("Journal of Extremely Obscure Studies") == "Other"
    assert venue_slug("Journal of Extremely Obscure Studies") == "Other"


def test_empty_venue():
    assert canonical_venue(None) == ""
    assert venue_slug("") == "Other"


def test_venue_matches_canonical_and_alias():
    assert venue_matches("IEEE Transactions on Geoscience and Remote Sensing", ["IEEE TGRS"])
    assert venue_matches("TGRS", ["IEEE TGRS"])
    assert not venue_matches("CVPR", ["IEEE TGRS"])
    assert venue_matches("anything", [])


def test_user_config_venue_list_is_union_with_defaults(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "venues:\n  - My Custom Venue\nscroll:\n  providers: [openalex]\n",
        encoding="utf-8",
    )
    settings = load_settings(home=tmp_path)
    venues = settings.venues
    assert venues[0] == "My Custom Venue"
    assert "IEEE GRSL" in venues
    assert "IEEE JSTARS" in venues
    assert "Remote Sensing (MDPI)" in venues
    # No duplicates.
    assert len(venues) == len(set(venues))
