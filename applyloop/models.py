"""Data models for applyloop."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class ApplicationStatus(Enum):
    EVALUATED = "Evaluated"
    APPLIED = "Applied"
    RESPONDED = "Responded"
    INTERVIEW = "Interview"
    OFFER = "Offer"
    REJECTED = "Rejected"
    DISCARDED = "Discarded"
    SKIP = "SKIP"

    @classmethod
    def from_string(cls, raw: str) -> "ApplicationStatus":
        """Resolve a raw status string (including aliases) to canonical status."""
        cleaned = re.sub(r"\*\*", "", raw).strip()
        cleaned = re.sub(r"\s+\d{4}-\d{2}-\d{2}.*$", "", cleaned).strip()
        lower = cleaned.lower()

        canonical_map = {s.value.lower(): s for s in cls}
        if lower in canonical_map:
            return canonical_map[lower]

        aliases: dict[str, "ApplicationStatus"] = {
            "evaluada": cls.EVALUATED,
            "aplicado": cls.APPLIED,
            "enviada": cls.APPLIED,
            "aplicada": cls.APPLIED,
            "sent": cls.APPLIED,
            "respondido": cls.RESPONDED,
            "entrevista": cls.INTERVIEW,
            "oferta": cls.OFFER,
            "rechazado": cls.REJECTED,
            "rechazada": cls.REJECTED,
            "descartado": cls.DISCARDED,
            "descartada": cls.DISCARDED,
            "cerrada": cls.DISCARDED,
            "cancelada": cls.DISCARDED,
            "no aplicar": cls.SKIP,
            "no_aplicar": cls.SKIP,
            "skip": cls.SKIP,
            "monitor": cls.SKIP,
            "condicional": cls.EVALUATED,
            "hold": cls.EVALUATED,
            "evaluar": cls.EVALUATED,
            "verificar": cls.EVALUATED,
            "geo blocker": cls.SKIP,
        }

        if lower in aliases:
            return aliases[lower]

        if re.match(r"^(duplicado|dup|repost)", lower):
            return cls.DISCARDED

        return cls.EVALUATED

    @property
    def rank(self) -> int:
        """Pipeline advancement rank (higher = further along)."""
        ranks = {
            self.SKIP: 0,
            self.DISCARDED: 0,
            self.REJECTED: 1,
            self.EVALUATED: 2,
            self.APPLIED: 3,
            self.RESPONDED: 4,
            self.INTERVIEW: 5,
            self.OFFER: 6,
        }
        return ranks.get(self, 0)

    @property
    def dashboard_group(self) -> str:
        return self.value.lower()


@dataclass
class Application:
    num: int
    date: str
    company: str
    role: str
    score: str
    status: str
    pdf: str
    report: str
    notes: str = ""
    raw_line: str = ""

    @classmethod
    def from_pipe_line(cls, line: str) -> Optional["Application"]:
        """Parse a pipe-delimited markdown table row."""
        parts = [s.strip() for s in line.split("|")]
        if len(parts) < 9:
            return None
        try:
            num = int(parts[1])
        except (ValueError, IndexError):
            return None
        if num == 0:
            return None
        return cls(
            num=num,
            date=parts[2],
            company=parts[3],
            role=parts[4],
            score=parts[5],
            status=parts[6],
            pdf=parts[7],
            report=parts[8],
            notes=parts[9] if len(parts) > 9 else "",
            raw_line=line,
        )

    def to_pipe_line(self) -> str:
        return (
            f"| {self.num} | {self.date} | {self.company} | {self.role} "
            f"| {self.score} | {self.status} | {self.pdf} | {self.report} "
            f"| {self.notes} |"
        )

    @property
    def parsed_score(self) -> float:
        m = re.search(r"([\d.]+)", re.sub(r"\*\*", "", self.score))
        return float(m.group(1)) if m else 0.0

    @property
    def report_num(self) -> Optional[int]:
        m = re.search(r"\[(\d+)\]", self.report)
        return int(m.group(1)) if m else None

    @property
    def normalized_company(self) -> str:
        return re.sub(r"[^a-z0-9]", "", self.company.lower())

    @property
    def parsed_date(self) -> Optional[date]:
        try:
            return datetime.strptime(self.date.strip(), "%Y-%m-%d").date()
        except (ValueError, AttributeError):
            return None

    @property
    def canonical_status(self) -> ApplicationStatus:
        return ApplicationStatus.from_string(self.status)


@dataclass
class Reminder:
    application_num: int
    company: str
    role: str
    remind_date: str
    reminder_type: str  # "follow_up", "interview_prep", "deadline"
    message: str = ""
    completed: bool = False

    def to_dict(self) -> dict:
        return {
            "application_num": self.application_num,
            "company": self.company,
            "role": self.role,
            "remind_date": self.remind_date,
            "reminder_type": self.reminder_type,
            "message": self.message,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Reminder":
        return cls(**d)


@dataclass
class ArchetypeFit:
    name: str
    level: str
    fit: str  # primary, secondary, adjacent


@dataclass
class CandidateProfile:
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    portfolio_url: str = ""
    github: str = ""
    twitter: str = ""
    headline: str = ""
    exit_story: str = ""
    superpowers: list[str] = field(default_factory=list)
    target_roles: list[str] = field(default_factory=list)
    archetypes: list[ArchetypeFit] = field(default_factory=list)
    target_comp: str = ""
    minimum_comp: str = ""
    currency: str = "USD"
    country: str = ""
    city: str = ""
    timezone: str = ""

    @classmethod
    def from_yaml(cls, data: dict) -> "CandidateProfile":
        candidate = data.get("candidate", {})
        narrative = data.get("narrative", {})
        compensation = data.get("compensation", {})
        location = data.get("location", {})
        target_roles = data.get("target_roles", {})

        archetypes = []
        for a in target_roles.get("archetypes", []):
            archetypes.append(ArchetypeFit(
                name=a.get("name", ""),
                level=a.get("level", ""),
                fit=a.get("fit", "adjacent"),
            ))

        return cls(
            full_name=candidate.get("full_name", ""),
            email=candidate.get("email", ""),
            phone=candidate.get("phone", ""),
            location=candidate.get("location", ""),
            linkedin=candidate.get("linkedin", ""),
            portfolio_url=candidate.get("portfolio_url", ""),
            github=candidate.get("github", ""),
            twitter=candidate.get("twitter", ""),
            headline=narrative.get("headline", ""),
            exit_story=narrative.get("exit_story", ""),
            superpowers=narrative.get("superpowers", []),
            target_roles=target_roles.get("primary", []),
            archetypes=archetypes,
            target_comp=compensation.get("target_range", ""),
            minimum_comp=compensation.get("minimum", ""),
            currency=compensation.get("currency", "USD"),
            country=location.get("country", ""),
            city=location.get("city", ""),
            timezone=location.get("timezone", ""),
        )


def role_fuzzy_match(a: str, b: str) -> bool:
    """Check if two role titles are fuzzy matches (share >= 2 significant words)."""
    words_a = [w for w in a.lower().split() if len(w) > 3]
    words_b = [w for w in b.lower().split() if len(w) > 3]
    overlap = [w for w in words_a if any(wb in w or w in wb for wb in words_b)]
    return len(overlap) >= 2
