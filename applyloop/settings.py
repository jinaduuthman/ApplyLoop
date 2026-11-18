"""Agent settings — loads config/applyloop.yml (falls back to the packaged example)."""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import ProjectPaths


def _expand(p: str | Path, base: Path | None = None) -> Path:
    path = Path(str(p)).expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path


@dataclass
class Track:
    id: str
    label: str = ""
    cv: Path | None = None
    fallback_pdf: Path | None = None
    max_pages: int = 2
    min_score: float = 3.5
    queries: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    short: str = ""                     # file-name tag: JaneSmith_<short>_Base.pdf
    paused: bool = False                # `paused: true` → not searched/applied; old jobs keep their CV

    @property
    def tag(self) -> str:
        if self.short:
            return self.short
        known = {"data_engineering": "DE", "ml_engineering": "MLE", "research_scientist": "ResearchScientist"}
        return known.get(self.id) or "".join(w[:1].upper() for w in re.split(r"[_\W]+", self.id) if w) or "CV"

    @property
    def is_tex(self) -> bool:
        return bool(self.cv and self.cv.suffix.lower() == ".tex")


@dataclass
class Settings:
    root: Path
    tracks: list[Track]
    search: dict[str, Any]
    filters: dict[str, Any]
    apply: dict[str, Any]
    browser: dict[str, Any]
    llm: dict[str, Any]
    storage: dict[str, Any]
    sheets: dict[str, Any]
    cv_dir: Path | None = None
    email_alerts: dict = field(default_factory=dict)
    all_tracks: list[Track] = field(default_factory=list)   # incl. paused ones

    def track(self, track_id: str) -> Track | None:
        return next((t for t in (self.all_tracks or self.tracks) if t.id == track_id), None)

    def is_active(self, track_id: str | None) -> bool:
        """False only for a track marked `paused: true`."""
        tr = self.track(track_id) if track_id else None
        return not (tr and tr.paused)

    @property
    def db_path(self) -> Path:
        p = _expand(self.storage.get("db", "data/applyloop.db"), self.root)
        from .config import prefer_existing
        return prefer_existing(p, p.with_name("jobpilot.db")) if p.name == "applyloop.db" else p

    @property
    def apply_mode(self) -> str:
        return str(self.apply.get("mode", "review")).lower()


DEFAULTS: dict[str, dict[str, Any]] = {
    "search": {"sources": ["indeed", "glassdoor"], "location": "", "remote": True,
               "work_mode": "any", "remote_only": False,
               "posted_within_days": 7, "max_per_query": 30, "easy_apply_only": False,
               "companies": []},
    "filters": {"title_exclude": [], "company_blocklist": [], "description_exclude": [],
                "min_salary": 0},
    "apply": {"mode": "review", "daily_cap": 20, "delay_seconds": [45, 120],
              "cover_letter": "required", "tailor": "auto", "screenshots": True,
              "auto": {"min_score": 4.0, "max_per_run": 10, "allow_ai_answers": False, "headless": False}},
    "browser": {"engine": "chromium", "mode": "profile", "profile_dir": "~/.applyloop/chrome-profile",
                "cdp_url": "http://127.0.0.1:9222", "channel": "chrome", "headless": False},
    "llm": {"provider": "auto", "model": ""},
    "storage": {"db": "data/applyloop.db"},
    "email_alerts": {"imap_host": "imap.gmail.com", "folder": "INBOX", "lookback_days": 3,
                     "senders": ["indeed.com", "glassdoor.com", "linkedin.com"],
                     "find_on_company_site": True},
    "sheets": {"enabled": False, "spreadsheet_id": "", "worksheet": "Applications",
               "service_account_json": "~/.applyloop/google-service-account.json"},
}


def settings_path(paths: ProjectPaths) -> Path:
    from .config import STARTER_DIR, prefer_existing
    return prefer_existing(paths.config_dir / "applyloop.yml", paths.config_dir / "jobpilot.yml",
                           STARTER_DIR / "applyloop.example.yml")


def load_settings(paths: ProjectPaths | None = None, path: Path | None = None) -> Settings:
    paths = paths or ProjectPaths()
    cfg_path = path or settings_path(paths)
    data: dict[str, Any] = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    cv_dir = _expand(data["cv_dir"], paths.root) if data.get("cv_dir") else None

    tracks = []
    for t in data.get("tracks", []) or []:
        tracks.append(Track(
            id=str(t["id"]),
            label=t.get("label", t["id"]),
            cv=_expand(t["cv"], cv_dir) if t.get("cv") else None,
            fallback_pdf=_expand(t["fallback_pdf"], cv_dir) if t.get("fallback_pdf") else None,
            max_pages=int(t.get("max_pages", 2)),
            min_score=float(t.get("min_score", 3.5)),
            queries=list(t.get("queries", [])),
            titles=[s.lower() for s in t.get("titles", [])],
            keywords=[s.lower() for s in t.get("keywords", [])],
            short=str(t.get("short", "") or ""),
            paused=bool(t.get("paused", False)),
        ))

    sections = {}
    for key, default in DEFAULTS.items():
        merged = dict(default)
        merged.update(data.get(key) or {})
        sections[key] = merged

    active = [t for t in tracks if not t.paused] or tracks
    return Settings(root=paths.root, tracks=active, cv_dir=cv_dir, all_tracks=tracks, **sections)
