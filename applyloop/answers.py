"""Answer screening questions: profile → answer bank → (optional) AI draft.

Every answer carries a `source`:
  profile / bank / eeo   trusted — may be auto-submitted in queue mode
  ai                     drafted by the model — shown to you; never auto-submitted
  none                   unknown — saved so you can add a rule (`applyloop answers pending`)
Matching uses whole-word regexes, so "ethnicity" no longer matches "city" and
"separate" no longer matches "rate".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .eeo import EEO
from .llm import LLM
from .models import CandidateProfile

TRUSTED = {"profile", "bank", "eeo"}

EEO_RE = re.compile(r"\b(gender|sex|race|racial|ethnicity|ethnic|hispanic|latin[oa]|veteran|"
                    r"disabilit(y|ies)|sexual orientation|transgender|pronouns?)\b", re.I)
DECLINE_RE = re.compile(r"decline|prefer not|don.?t wish|do not wish|choose not|not to (say|disclose|answer|self)|"
                        r"rather not|i don.?t want", re.I)


@dataclass
class Answer:
    value: str
    source: str

    @property
    def trusted(self) -> bool:
        return self.source in TRUSTED


def _rx(pattern: str) -> re.Pattern:
    return re.compile(r"(?<!\w)(?:" + pattern + r")(?!\w)", re.I)


def _profile_rules(p: CandidateProfile) -> list[tuple[re.Pattern, str]]:
    parts = (p.full_name or "").split()
    first, last = (parts[0] if parts else ""), (" ".join(parts[1:]) if len(parts) > 1 else "")
    city = p.city or (p.location.split(",")[0] if p.location else "")
    loc = p.location or ", ".join(x for x in (p.city, p.country) if x)
    return [
        (_rx(r"first name|given name|legal first name|preferred first name"), first),
        (_rx(r"last name|family name|surname|legal last name"), last),
        (_rx(r"full name|your name|^name\*?$|legal name"), p.full_name),
        (_rx(r"e-?mail( address)?"), p.email),
        (_rx(r"phone( number)?|mobile( number)?|telephone|cell"), p.phone),
        (_rx(r"linkedin( profile| url)?"), p.linkedin),
        (_rx(r"github( profile| url)?"), p.github),
        (_rx(r"portfolio|personal (website|site)|website"), p.portfolio_url or p.github),
        (_rx(r"city"), city),
        (_rx(r"(current )?location|where are you (located|based)|address"), loc),
        (_rx(r"country"), p.country),
    ]


class AnswerBank:
    def __init__(self, profile: CandidateProfile, rules: list[dict] | None = None,
                 defaults: dict | None = None, eeo_answer: str = "decline",
                 llm: LLM | None = None, cv_text: str = "", eeo: EEO | None = None,
                 track_id: str = ""):
        self.profile = profile
        self.track_id = track_id
        self._company = ""
        self.defaults = {"target_comp": profile.target_comp, **(defaults or {})}
        # an answer may be a list of acceptable alternatives, tried in order against the options
        self.rules = [([_rx(m) for m in r.get("match", [])],
                       [str(a) for a in r["answer"]] if isinstance(r.get("answer"), list) else str(r.get("answer", "")))
                      for r in (rules or [])]
        self.eeo_answer = eeo_answer
        self.eeo = eeo or EEO({k: eeo_answer for k in EEO().answers})
        self.llm = llm
        self.cv_text = cv_text
        self._profile_rules = _profile_rules(profile)

    @classmethod
    def load(cls, path: Path, profile: CandidateProfile, llm: LLM | None = None,
             cv_text: str = "", track_id: str = "") -> "AnswerBank":
        data = {}
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        legacy = str(data.get("eeo_answer", "decline"))
        eeo = EEO.load(path.parent / "eeo.yml", legacy)
        return cls(profile, data.get("rules", []), data.get("defaults", {}), legacy, llm,
                   cv_text, eeo, track_id)

    def _expand(self, alts: list[str]) -> list[str]:
        """An answer that is just "{name}" whose default (per track) is a list → its items,
        tried in order (e.g. highest_degree: {ml_engineering: ["Doctor of Philosophy (Ph.D.)", "PhD"]})."""
        out: list[str] = []
        for a in alts:
            m = re.fullmatch(r"\{(\w+)\}", a.strip())
            v = self.defaults.get(m.group(1)) if m else None
            if isinstance(v, dict):
                v = v.get(self.track_id, v.get("default", ""))
            if isinstance(v, list):
                out += [str(x) for x in v]
            else:
                out.append(a)
        return out

    def _fmt(self, template: str) -> str:
        """Fill {placeholders}. A default may be a per-track mapping
        ({data_engineering: ..., default: ...}); {previously_employed} is Yes/No depending on
        whether the job's company is in defaults.past_employers."""
        vals = {}
        for k, v in self.defaults.items():
            if isinstance(v, dict):
                v = v.get(self.track_id, v.get("default", ""))
            vals[k] = v
        past = [str(x).lower() for x in (self.defaults.get("past_employers") or [])]
        comp = (self._company or "").lower()
        vals["previously_employed"] = "Yes" if comp and any(p and (p in comp or comp in p) for p in past) else "No"
        try:
            return template.format(**vals)
        except (KeyError, IndexError, ValueError):
            return template

    def resolve(self, question: str, field_type: str = "text", options: list[str] | None = None,
                job_title: str = "", company: str = "", allow_ai: bool = True) -> Answer:
        q = " ".join((question or "").split())
        self._company = company or self._company
        opts = [o for o in (options or []) if o and o.strip()]

        eeo = self.eeo.answer(q, opts)
        if eeo is not None:
            return Answer(*eeo)

        # Bank rules first (they're specific: sponsorship, authorization, ...), then the short
        # profile rules. Long free-text prompts ("Why do you want to work here?") skip the
        # profile rules. If a value doesn't fit the field's options, keep looking.
        # every rule that matches, most specific first (longest matched phrase), so
        # "require sponsorship … right to work" goes to the sponsorship rule, not authorization
        hits = []
        for i, (patterns, template) in enumerate(self.rules):
            spans = [len(m.group(0)) for p in patterns for m in [p.search(q)] if m]
            if spans:
                hits.append((-max(spans), i, template))
        for _, _, template in sorted(hits):
            if True:
                for alt in self._expand(template if isinstance(template, list) else [template]):
                    ans = self._fit(self._fmt(alt), opts, "bank")
                    if ans.value:
                        return ans
        if len(q) < 60:
            for rx, value in self._profile_rules:
                if value and rx.search(q):
                    for alt in _aliases(value):
                        ans = self._fit(alt, opts, "profile")
                        if ans.value:
                            return ans

        if allow_ai and self.llm and self.llm.available:
            return self._ai(q, field_type, opts, job_title, company)
        return Answer("", "none")

    def _fit(self, value: str, opts: list[str], source: str) -> Answer:
        if not opts:
            return Answer(value, source)
        pick = pick_option(value, opts)
        return Answer(pick, source) if pick else Answer("", "none")

    def _ai(self, q: str, field_type: str, opts: list[str], title: str, company: str) -> Answer:
        p = self.profile
        prompt = (
            f"Answer this job-application question for the candidate, truthfully and concisely, "
            f"using ONLY the facts below. If the facts don't contain the answer, reply exactly UNKNOWN.\n\n"
            f"Question: {q}\nField type: {field_type}\n"
            + (f"Options (reply with one exactly): {opts}\n" if opts else "")
            + f"Job: {title} at {company}\n\nCandidate: {p.full_name}; {p.headline}\n"
            f"Location: {p.location or p.city}\nCV:\n{self.cv_text[:6000]}\n\n"
            f"Reply with the answer text only (1-3 sentences for free text).")
        try:
            raw = self.llm.complete(prompt, max_tokens=300).strip().strip('"')
        except Exception:
            return Answer("", "none")
        if not raw or raw.upper().startswith("UNKNOWN"):
            return Answer("", "none")
        if opts:
            pick = pick_option(raw, opts)
            return Answer(pick, "ai") if pick else Answer("", "none")
        return Answer(raw, "ai")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+]+", " ", (s or "").lower()).strip()


def pick_option(value: str, options: list[str]) -> str:
    """Map an answer like 'Yes' / '7' / "Master's" onto one of the form's options."""
    v = _norm(value)
    if not v:
        return ""
    norm = [(_norm(o), o) for o in options]
    for n, o in norm:
        if n == v:
            return o
    for n, o in norm:                     # "Yes" -> "Yes, I am authorized"
        if n.split()[:1] == v.split()[:1] and v.split()[0] in ("yes", "no"):
            return o
    if v.replace(" ", "").isdigit():      # years: pick the range containing the number
        num = int(v.replace(" ", ""))
        open_ended = []
        for n, o in norm:
            nums = [int(x) for x in re.findall(r"\d+", n)]
            if len(nums) == 2 and nums[0] <= num <= nums[1]:
                return o
            if len(nums) == 1 and nums[0] == num and "+" not in o:
                return o
            if len(nums) == 1 and ("+" in o or "more" in n or "over" in n) and num >= nums[0]:
                open_ended.append((nums[0], o))
        if open_ended:
            return max(open_ended)[1]
    if len(v) <= 3:                       # "US", "USA": exact only ("us" ⊂ "australia")
        return ""
    for n, o in norm:
        if v in n or (len(n) > 3 and n in v):
            return o
    return ""


US_ALIASES = ["United States", "United States of America", "USA", "US", "U.S.", "U.S.A."]


def _aliases(value: str) -> list[str]:
    """Country values get their common spellings, e.g. United States → USA / US."""
    if _norm(value) in {_norm(a) for a in US_ALIASES}:
        return [value] + [a for a in US_ALIASES if a != value]
    return [value]
