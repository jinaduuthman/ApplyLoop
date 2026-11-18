"""Voluntary self-identification (EEO) answers from config/eeo.yml.

Each category maps to either `decline` or a list of acceptable phrasings, most
preferred first. Options are matched by whole words, so "Male" never picks
"Female". If no phrasing matches the form's options, applyloop falls back to the
form's "decline / prefer not to say" option, and otherwise leaves it blank.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

DECLINE_RE = re.compile(r"decline|prefer not|don.?t wish|do not wish|choose not|not to (say|disclose|answer|self)|"
                        r"rather not|i don.?t want", re.I)

CATEGORIES: list[tuple[str, re.Pattern]] = [
    ("hispanic_latino", re.compile(r"\b(hispanic|latin[oax])\b", re.I)),
    ("race", re.compile(r"\b(race|racial|ethnicity|ethnic)\b", re.I)),
    ("transgender", re.compile(r"\btransgender\b", re.I)),
    ("gender", re.compile(r"\b(gender|sex)\b", re.I)),
    ("veteran", re.compile(r"\bveteran\b", re.I)),
    ("disability", re.compile(r"\bdisabilit(y|ies)\b", re.I)),
    ("sexual_orientation", re.compile(r"\bsexual orientation\b", re.I)),
    ("pronouns", re.compile(r"\bpronouns?\b", re.I)),
]

DEFAULTS = {c: "decline" for c, _ in CATEGORIES}


def _is_yes_no(options: list[str]) -> bool:
    low = {o.strip().lower().split(",")[0] for o in options}
    return bool(low & {"yes", "no"}) and len(options) <= 4


def category(question: str, options: list[str] | None = None) -> str | None:
    q = question or ""
    opts = options or []
    # "Are you Hispanic or Latino?" (yes/no) vs "Race/Ethnicity" select that lists Hispanic as an option
    if CATEGORIES[0][1].search(q) and (_is_yes_no(opts) or not CATEGORIES[1][1].search(q)):
        return "hispanic_latino"
    for name, rx in CATEGORIES[1:]:
        if rx.search(q):
            return name
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def match_option(phrases: list[str], options: list[str]) -> str:
    norm = [(_norm(o), o) for o in options]
    for p in phrases:
        np_ = _norm(p)
        if not np_:
            continue
        for n, o in norm:                       # exact
            if n == np_:
                return o
        rx = re.compile(r"(?<![a-z0-9])" + re.escape(np_) + r"(?![a-z0-9])")
        for n, o in norm:                       # whole-word containment
            if rx.search(n):
                return o
    return ""


class EEO:
    def __init__(self, answers: dict | None = None):
        self.answers = {**DEFAULTS, **(answers or {})}

    @classmethod
    def load(cls, path: Path, legacy_default: str = "decline") -> "EEO":
        data = {}
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        base = {c: legacy_default for c, _ in CATEGORIES}
        base.update(data)
        return cls(base)

    def answer(self, question: str, options: list[str] | None) -> tuple[str, str] | None:
        """Return (value, source) for an EEO question, or None if it isn't one."""
        cat = category(question, options)
        if cat is None:
            return None
        pref = self.answers.get(cat, "decline")
        phrases = [pref] if isinstance(pref, str) else [str(p) for p in pref]
        opts = [o for o in (options or []) if o and o.strip()]
        if not opts:                                       # free-text field
            if phrases[0].lower() == "decline":
                return "", "none"
            return phrases[0], "eeo"
        if phrases[0].lower() != "decline":
            pick = match_option(phrases, opts)
            if pick:
                return pick, "eeo"
        pick = next((o for o in opts if DECLINE_RE.search(o)), "")
        return (pick, "eeo") if pick else ("", "none")
