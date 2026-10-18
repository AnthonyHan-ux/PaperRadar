"""Configuration, paths and the default research profile.

Layout (all under ``home``):

    PaperRadar/
        config/config.yaml, research_profile.yaml
        library/<year>/<venue>/<file>.pdf
        database/paperradar.db
        logs/app.log
        cache/*.json
        tmp/*.part

``home`` is the executable directory when frozen (portable exe) and the project
root when running from source, overridable with ``PAPERRADAR_HOME``.
"""

from __future__ import annotations

import copy
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

APP_NAME = "PaperRadar"
APP_VERSION = "1.0.0"

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {"theme": "dark", "language": "zh", "log_level": "INFO"},
    "api": {
        "openalex_mailto": "",
        "unpaywall_email": "",
        "semantic_scholar_api_key": "",
        "user_agent": "PaperRadar/1.0 (+https://github.com/paperradar; academic use)",
        "timeout_seconds": 30,
        "max_retries": 3,
        "cache_ttl_hours": 24,
        "rate_limits": {
            "openalex": 0.15,
            "semantic_scholar": 1.2,
            "crossref": 0.2,
            "arxiv": 3.2,
            "unpaywall": 0.2,
            "openreview": 1.0,
        },
    },
    "search": {
        "providers": ["openalex", "semantic_scholar", "crossref", "arxiv"],
        "default_limit": 30,
        "per_provider_limit": 30,
        "parallel": True,
        "provider_timeout_seconds": 40,
        "title_dedupe_threshold": 95,
        "min_year": 1990,
    },
    "download": {
        "max_workers": 3,
        "min_pdf_bytes": 10000,
        "max_pdf_bytes": 250 * 1024 * 1024,
        "request_timeout_seconds": 90,
        "validate_title_threshold": 90,
        "validate_title_warning": 80,
        "enable_pdf_resume": True,
    },
    "scoring": {
        "weights": {
            "title": 40,
            "abstract": 30,
            "keywords": 15,
            "direction": 10,
            "venue": 5,
        },
        "saturation": {"title": 2, "abstract": 4, "keywords": 3, "direction": 1},
        "negative_penalty": 15,
        "max_negative_penalty": 30,
        "bands": {"high": 90, "medium": 70, "low": 50},
    },
    "venues": [
        "CVPR", "ICCV", "ECCV", "NeurIPS", "ICLR", "AAAI", "ACM MM",
        "TGRS", "IEEE TIP", "ISPRS JPRS", "Remote Sensing of Environment",
        "Information Fusion", "arXiv", "Other",
    ],
    "directions": [
        "Change Detection", "Optical-SAR", "Multimodal", "Disaster",
        "Building Damage", "Cloud Removal", "Fog", "Degradation",
        "Robustness", "Domain Generalization", "Foundation Model", "VLM",
        "Agent", "Long Time Series", "Generative Model",
    ],
}

DEFAULT_PROFILE: dict[str, Any] = {
    "name": "Degradation-Robust Multimodal Remote Sensing",
    "keywords": [
        "optical SAR", "change detection", "building damage", "degradation",
        "fog", "cloud", "noise", "reliability", "robust fusion",
        "remote sensing", "multimodal fusion",
    ],
    "positive_terms": [
        "cross-event", "generalization", "heterogeneous", "missing modality",
        "disaster", "foundation model", "vision-language", "SAR-optical",
        "all-weather", "low-light", "haze", "super-resolution",
    ],
    "negative_terms": [
        "medical", "autonomous driving", "infrared-visible fusion",
        "human action recognition",
    ],
    "priority_venues": [
        "ISPRS Journal of Photogrammetry and Remote Sensing",
        "Remote Sensing of Environment", "IEEE TGRS", "IEEE TIP",
        "CVPR", "ICCV", "ECCV", "NeurIPS",
    ],
}


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resource_root() -> Path:
    """Directory that holds bundled read-only assets (config templates)."""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent
    return project_root()


def default_home() -> Path:
    env = os.environ.get("PAPERRADAR_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        # The CLI ships in <app>/cli/ and must share the GUI's data directory.
        if exe_dir.name.lower() == "cli" and (exe_dir.parent / "PaperRadar.exe").exists():
            return exe_dir.parent
        return exe_dir
    return project_root()


@dataclass
class AppPaths:
    home: Path
    config_dir: Path = field(init=False)
    library_dir: Path = field(init=False)
    database_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)
    tmp_dir: Path = field(init=False)
    export_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.home = Path(self.home).resolve()
        self.config_dir = self.home / "config"
        self.library_dir = self.home / "library"
        self.database_dir = self.home / "database"
        self.logs_dir = self.home / "logs"
        self.cache_dir = self.home / "cache"
        self.tmp_dir = self.home / "tmp"
        self.export_dir = self.home / "exports"

    @property
    def db_path(self) -> Path:
        return self.database_dir / "paperradar.db"

    @property
    def config_path(self) -> Path:
        return self.config_dir / "config.yaml"

    @property
    def profile_path(self) -> Path:
        return self.config_dir / "research_profile.yaml"

    def ensure(self) -> "AppPaths":
        for directory in (
            self.home,
            self.config_dir,
            self.library_dir,
            self.database_dir,
            self.logs_dir,
            self.cache_dir,
            self.tmp_dir,
            self.export_dir,
        ):
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError:
                # The application stays usable for read-only operations; the
                # failing directory will surface as a clear error when used.
                pass
        return self


# --------------------------------------------------------------------------- #
# YAML helpers
# --------------------------------------------------------------------------- #
def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        import logging

        logging.getLogger(__name__).warning("cannot read %s: %s", path, exc)
        return {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False, indent=2)


def _ensure_default_file(target: Path, default_doc: dict[str, Any]) -> None:
    if target.exists():
        return
    try:
        write_yaml(target, default_doc)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    paths: AppPaths
    config: dict[str, Any]
    profile: dict[str, Any]

    # -- convenience accessors ------------------------------------------- #
    @property
    def api(self) -> dict[str, Any]:
        return self.config.get("api", {})

    @property
    def search_cfg(self) -> dict[str, Any]:
        return self.config.get("search", {})

    @property
    def download_cfg(self) -> dict[str, Any]:
        return self.config.get("download", {})

    @property
    def scoring_cfg(self) -> dict[str, Any]:
        return self.config.get("scoring", {})

    @property
    def app_cfg(self) -> dict[str, Any]:
        return self.config.get("app", {})

    @property
    def venues(self) -> list[str]:
        return list(self.config.get("venues") or [])

    @property
    def directions(self) -> list[str]:
        return list(self.config.get("directions") or [])

    @property
    def enabled_providers(self) -> list[str]:
        return [p for p in self.search_cfg.get("providers", []) if p]

    @property
    def mailto(self) -> str:
        return str(self.api.get("openalex_mailto") or "").strip()

    @property
    def unpaywall_email(self) -> str:
        return str(self.api.get("unpaywall_email") or self.mailto).strip()

    @property
    def user_agent(self) -> str:
        return str(self.api.get("user_agent") or "PaperRadar/1.0")

    @property
    def cache_ttl_seconds(self) -> int:
        try:
            return int(float(self.api.get("cache_ttl_hours", 24)) * 3600)
        except (TypeError, ValueError):
            return 24 * 3600

    @property
    def rate_limits(self) -> dict[str, float]:
        raw = self.api.get("rate_limits") or {}
        return {str(k): float(v) for k, v in raw.items() if v is not None}

    def save_profile(self, profile: dict[str, Any]) -> None:
        self.profile = profile
        write_yaml(self.paths.profile_path, profile)

    def save_config(self) -> None:
        write_yaml(self.paths.config_path, self.config)

    def profile_terms(self) -> list[str]:
        terms: list[str] = []
        for key in ("keywords", "positive_terms"):
            for term in self.profile.get(key, []) or []:
                if term and term not in terms:
                    terms.append(str(term))
        return terms

    def profile_negative_terms(self) -> list[str]:
        return [str(t) for t in (self.profile.get("negative_terms") or []) if t]

    def profile_venues(self) -> list[str]:
        return [str(v) for v in (self.profile.get("priority_venues") or []) if v]


def load_settings(
    home: Optional[Path] = None,
    config_path: Optional[Path] = None,
    profile_path: Optional[Path] = None,
    create_defaults: bool = True,
) -> Settings:
    paths = AppPaths(home or default_home())
    if create_defaults:
        paths.ensure()
        _ensure_default_file(paths.config_path, DEFAULT_CONFIG)
        _ensure_default_file(paths.profile_path, DEFAULT_PROFILE)

    user_config = read_yaml(Path(config_path) if config_path else paths.config_path)
    bundled_config = read_yaml(resource_root() / "config" / "config.yaml")
    config = _deep_merge(DEFAULT_CONFIG, bundled_config)
    config = _deep_merge(config, user_config)

    user_profile = read_yaml(Path(profile_path) if profile_path else paths.profile_path)
    bundled_profile = read_yaml(resource_root() / "config" / "research_profile.yaml")
    profile = _deep_merge(DEFAULT_PROFILE, bundled_profile)
    profile = _deep_merge(profile, user_profile)

    return Settings(paths=paths, config=config, profile=profile)
