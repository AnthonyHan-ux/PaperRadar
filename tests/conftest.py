"""Shared pytest fixtures. All tests must run offline and without Qt."""

from __future__ import annotations

import os
import logging
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Tests must never touch the user's real data directory.
os.environ.setdefault("PAPERRADAR_HOME", str(ROOT / ".pytest-home"))
# Offscreen Qt so GUI smoke tests work without a desktop session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.paper import OaStatus, PdfCandidate, Paper  # noqa: E402
from utils.config import load_settings  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_logging_handlers():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture()
def settings(tmp_path):
    cfg = load_settings(home=tmp_path, create_defaults=True)
    # Existing domain-specific scoring tests use an explicit example profile;
    # new installations have discipline-neutral defaults.
    cfg.profile = yaml.safe_load((ROOT / 'examples/research_profile_remote_sensing.yaml').read_text(encoding='utf-8'))
    example = yaml.safe_load((ROOT / 'examples/config_remote_sensing.yaml').read_text(encoding='utf-8'))
    cfg.config['venues'] = example['venues']
    cfg.config['directions'] = example['directions']
    cfg.config["api"]["openalex_mailto"] = "tester@example.com"
    cfg.config["api"]["unpaywall_email"] = "tester@example.com"
    cfg.config["download"]["min_pdf_bytes"] = 2000
    return cfg


@pytest.fixture()
def sample_paper() -> Paper:
    return Paper(
        title=(
            "Beyond Attention: Convolutional Global Context for "
            "Remote Sensing Change Detection"
        ),
        authors=["Wei Zhang", "Li Wang", "Ming Chen"],
        year=2026,
        venue="ISPRS Journal of Photogrammetry and Remote Sensing",
        doi="10.1016/j.isprsjprs.2026.01.001",
        arxiv_id="2601.01234",
        abstract=(
            "Optical SAR change detection under fog and cloud degradation remains "
            "challenging. We propose a robust fusion backbone for building damage "
            "assessment across heterogeneous sensors."
        ),
        keywords=["change detection", "optical SAR", "remote sensing"],
        url="https://example.org/paper",
        source_url="https://doi.org/10.1016/j.isprsjprs.2026.01.001",
        pdf_candidates=[
            PdfCandidate(url="https://arxiv.org/pdf/2601.01234", provider="arXiv")
        ],
        oa_status=OaStatus.OPEN_ACCESS,
        sources=["openalex", "arxiv"],
    )


@pytest.fixture()
def make_paper():
    def _make(title: str, **kwargs) -> Paper:
        defaults = {
            "authors": ["Test Author"],
            "year": 2024,
            "venue": "CVPR",
        }
        defaults.update(kwargs)
        return Paper(title=title, **defaults)

    return _make
