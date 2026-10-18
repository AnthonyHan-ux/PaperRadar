"""Configuration, paths and the default research profile.

Runtime data (database, library, logs, cache, tmp, exports, user config) lives
**outside** the program directory, by default in:

    Windows : %LOCALAPPDATA%\\PaperRadar\\data
    Linux   : ~/.local/share/PaperRadar/data

This is what makes the application safe to run from ``Program Files``, a USB
stick or a read-only distribution folder, and it removes the V1.0 collision
between the ``database/`` source package and the runtime ``database/`` folder.

Layout (all under ``data root``):

    data/
        config/user_config.yaml, research_profile.yaml
        library/<year>/<venue>/<file>.pdf
        database/paperradar.db
        logs/app.log
        cache/*.json
        tmp/*.part
        exports/

``PAPERRADAR_HOME`` overrides the data root (tests and portable installs).
"""

from __future__ import annotations

import copy
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger(__name__)

APP_NAME = "PaperRadar"
APP_VERSION = "1.1.0"

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {"theme": "dark", "language": "zh", "log_level": "INFO"},
    "api": {
        "openalex_mailto": "",
        "unpaywall_email": "",
        "semantic_scholar_api_key": "",
        "user_agent": "PaperRadar/1.1 (+https://github.com/paperradar; academic use)",
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
        "IEEE TGRS", "IEEE TIP", "IEEE JSTARS", "IEEE GRSL",
        "ISPRS JPRS", "Remote Sensing of Environment", "Remote Sensing (MDPI)",
        "JAG", "Photogrammetric Engineering & Remote Sensing",
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


def default_data_root() -> Path:
    """Where user data lives.  Override with ``PAPERRADAR_HOME``."""
    env = os.environ.get("PAPERRADAR_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return (root / APP_NAME / "data").resolve()
    return (Path.home() / ".local" / "share" / APP_NAME / "data").resolve()


def program_dir() -> Path:
    """Directory holding the executable (frozen) or the source checkout."""
    if is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        # The CLI ships in <app>/cli/ and shares the GUI's program directory.
        if exe_dir.name.lower() == "cli" and (exe_dir.parent / "PaperRadar.exe").exists():
            return exe_dir.parent
        return exe_dir
    return project_root()


def legacy_home_candidates() -> list[Path]:
    """Directories that a V1.0 installation may have stored data in."""
    candidates: list[Path] = [program_dir()]
    if not is_frozen():
        candidates.append(project_root())
    unique: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - weird paths
            continue
        if resolved not in unique:
            unique.append(resolved)
    return unique


def looks_like_legacy_home(path: Path) -> bool:
    """True only when the directory really holds V1.0 *user data*.

    A source checkout always has ``config/config.yaml`` (the bundled template),
    so config alone is not evidence; a frozen program directory is different.
    """
    try:
        if (path / "database" / "paperradar.db").is_file():
            return True
        library = path / "library"
        if library.is_dir() and next(iter(library.rglob("*.pdf")), None) is not None:
            return True
        if is_frozen() and (path / "config" / "research_profile.yaml").is_file():
            return True
    except OSError:  # pragma: no cover
        return False
    return False


def find_legacy_home(target: Path) -> Optional[Path]:
    for candidate in legacy_home_candidates():
        if candidate == target:
            continue
        if looks_like_legacy_home(candidate):
            return candidate
    return None


# Set by resolve_home() so the GUI/CLI can report what happened.
LAST_MIGRATION: Any = None


def resolve_home(migrate: bool = True) -> Path:
    """Return the data root, migrating a V1.0 installation when needed.

    Falls back to the legacy directory if migration fails, so the application
    always starts with the user's existing data rather than an empty library.
    """
    global LAST_MIGRATION
    target = default_data_root()
    if os.environ.get("PAPERRADAR_HOME") or not migrate:
        return target

    from services.migration_service import MigrationService  # local: avoids a cycle

    if MigrationService.marker_path(target).exists():
        return target
    legacy = find_legacy_home(target)
    if legacy is None:
        return target

    report = MigrationService(legacy, target).run()
    LAST_MIGRATION = report
    if report.success:
        log.info("migrated V1.0 data from %s to %s", legacy, target)
        return target

    log.error(
        "data migration from %s failed (%s); continuing with the legacy data directory",
        legacy, report.error,
    )
    return legacy


def default_home() -> Path:
    """Backwards-compatible alias for the resolved data root."""
    return resolve_home()


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
        """User overrides (created on demand, never overwritten by upgrades)."""
        return self.config_dir / "user_config.yaml"

    @property
    def legacy_config_path(self) -> Path:
        """V1.0 user configuration, still read for backwards compatibility."""
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


def _union_list(current: Any, defaults: Any) -> list[Any]:
    """Union two option lists, keeping the user's order first.

    Venue/direction lists grow between releases; a user config written for
    V1.0 must still see the new options without editing YAML.
    """
    result: list[Any] = []
    for item in list(current or []) + list(defaults or []):
        if item not in result:
            result.append(item)
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


USER_CONFIG_TEMPLATE = """\
# PaperRadar user configuration.
# Values here override the built-in defaults; delete a key to fall back.
# Keep this file in %LOCALAPPDATA%\\PaperRadar\\data\\config\\.

api:
  # Fill these in to join the polite pools and enable Unpaywall.
  openalex_mailto: ""
  unpaywall_email: ""
  semantic_scholar_api_key: ""
"""


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
    migrate: bool = True,
) -> Settings:
    resolved_home = Path(home) if home is not None else resolve_home(migrate=migrate)
    paths = AppPaths(resolved_home)
    if create_defaults:
        paths.ensure()
        if not paths.config_path.exists():
            try:
                paths.config_path.write_text(USER_CONFIG_TEMPLATE, encoding="utf-8")
            except OSError:  # pragma: no cover - read-only dir
                pass
        _ensure_default_file(paths.profile_path, DEFAULT_PROFILE)

    config = _deep_merge(DEFAULT_CONFIG, read_yaml(resource_root() / "config" / "config.yaml"))
    if config_path is not None:
        config = _deep_merge(config, read_yaml(Path(config_path)))
    else:
        # V1.0 stored user settings in config/config.yaml; keep reading it so an
        # upgrade never silently resets someone's API keys.
        if paths.legacy_config_path != paths.config_path and paths.legacy_config_path.exists():
            config = _deep_merge(config, read_yaml(paths.legacy_config_path))
        config = _deep_merge(config, read_yaml(paths.config_path))
    config["venues"] = _union_list(config.get("venues"), DEFAULT_CONFIG["venues"])
    config["directions"] = _union_list(config.get("directions"), DEFAULT_CONFIG["directions"])

    profile = _deep_merge(DEFAULT_PROFILE, read_yaml(resource_root() / "config" / "research_profile.yaml"))
    profile = _deep_merge(
        profile,
        read_yaml(Path(profile_path)) if profile_path else read_yaml(paths.profile_path),
    )

    return Settings(paths=paths, config=config, profile=profile)
