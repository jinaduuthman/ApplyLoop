"""Configuration and path management for applyloop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import CandidateProfile


PKG_DIR = Path(__file__).resolve().parent
STARTER_DIR = PKG_DIR / "starter"          # templates copied by `applyloop init`
ASSETS_DIR = PKG_DIR / "assets"            # cv-template.html, states.yml, fonts/

_MARKERS = ("applyloop.yml", "profile.yml", "jobpilot.yml")


def find_project_root(start: Path | None = None) -> Path:
    """Your applyloop folder: $APPLYLOOP_HOME, else the nearest folder (from start/cwd upwards)
    holding config/applyloop.yml (or a source checkout), else the current folder."""
    import os
    home = os.environ.get("APPLYLOOP_HOME")
    if home:
        return Path(home).expanduser().resolve()
    current = start or Path.cwd()
    for parent in [current, *current.parents]:
        cfg = parent / "config"
        if cfg.is_dir() and (any((cfg / m).exists() for m in _MARKERS)
                             or (parent / "pyproject.toml").exists()):
            return parent
    return current


def prefer_existing(new: Path, *old: Path) -> Path:
    """`new` unless it doesn't exist yet and an older-named path does (pre-rename installs)."""
    if new.exists():
        return new
    return next((o for o in old if o.exists()), new)


def import_legacy_env() -> None:
    """Old JOBPILOT_* variables keep working as APPLYLOOP_*."""
    import os
    for k, v in list(os.environ.items()):
        if k.startswith("JOBPILOT_"):
            os.environ.setdefault("APPLYLOOP_" + k[len("JOBPILOT_"):], v)


class ProjectPaths:
    """Centralized path resolution for the applyloop project."""

    def __init__(self, root: Path | None = None):
        self.root = root or find_project_root()

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def profile_example(self) -> Path:
        return STARTER_DIR / "profile.example.yml"

    @property
    def profile(self) -> Path:
        return self.config_dir / "profile.yml"

    @property
    def cv(self) -> Path:
        return self.root / "cv.md"

    @property
    def portals(self) -> Path:
        return self.root / "portals.yml"

    @property
    def portals_example(self) -> Path:
        return prefer_existing(self.root / "templates" / "portals.example.yml",
                               ASSETS_DIR / "templates" / "portals.example.yml")

    @property
    def states(self) -> Path:
        return prefer_existing(self.root / "templates" / "states.yml", self.root / "states.yml",
                               ASSETS_DIR / "templates" / "states.yml")

    @property
    def cv_template(self) -> Path:
        return prefer_existing(self.root / "templates" / "cv-template.html",
                               ASSETS_DIR / "templates" / "cv-template.html")

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def applications(self) -> Path:
        p = self.data_dir / "applications.md"
        return p if p.exists() else self.root / "applications.md"

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def fonts_dir(self) -> Path:
        return prefer_existing(self.root / "fonts", ASSETS_DIR / "fonts")

    @property
    def batch_dir(self) -> Path:
        return self.root / "batch"

    @property
    def tracker_additions_dir(self) -> Path:
        return self.batch_dir / "tracker-additions"

    @property
    def merged_dir(self) -> Path:
        return self.tracker_additions_dir / "merged"

    @property
    def modes_dir(self) -> Path:
        return self.root / "modes"

    @property
    def version_file(self) -> Path:
        return self.root / "VERSION"

    @property
    def reminders_file(self) -> Path:
        return self.data_dir / "reminders.json"

    @property
    def scan_history(self) -> Path:
        return self.data_dir / "scan-history.tsv"

    @property
    def application_log(self) -> Path:
        return self.data_dir / "application-log.json"

    def ensure_dirs(self) -> list[str]:
        """Create required directories, return list of auto-created ones."""
        created = []
        for d in [self.data_dir, self.output_dir, self.reports_dir]:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created.append(d.name)
        return created


def load_profile(paths: ProjectPaths) -> CandidateProfile | None:
    """Load and parse config/profile.yml."""
    if not paths.profile.exists():
        return None
    with open(paths.profile) as f:
        data = yaml.safe_load(f)
    if not data:
        return None
    return CandidateProfile.from_yaml(data)


def load_states(paths: ProjectPaths) -> list[dict[str, Any]]:
    """Load canonical application states from states.yml."""
    if not paths.states.exists():
        return []
    with open(paths.states) as f:
        data = yaml.safe_load(f)
    return data.get("states", []) if data else []


def load_portals(paths: ProjectPaths) -> dict[str, Any]:
    """Load portals.yml configuration."""
    if not paths.portals.exists():
        return {}
    with open(paths.portals) as f:
        return yaml.safe_load(f) or {}
