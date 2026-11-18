"""Job sources. Browser sources (Indeed, Glassdoor) use the agent's browser session;
ATS sources (Greenhouse, Lever, Ashby) use public JSON APIs — no scraping, no login."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_RATING = re.compile(r"\s*[★☆]?\s*\b[1-5]\.\d\s*[★☆]?\s*$")


def clean_company(name: str) -> str:
    """'D&G (VA) 4.8 ★' → 'D&G (VA)' (job boards append the employer's star rating)."""
    return _RATING.sub("", " ".join((name or "").split())).strip(" -·|•")


def clean_title(title: str) -> str:
    """'4.8 — Senior Data Engineer' → 'Senior Data Engineer'."""
    return re.sub(r"^\s*[★☆]?\s*[1-5]\.\d\s*[★☆]?\s*[-–—|·:]?\s*", "", " ".join((title or "").split()))


@dataclass
class JobPosting:
    url: str
    title: str
    company: str
    location: str = ""
    salary: str = ""
    posted: str = ""
    source: str = ""
    easy_apply: bool = False
    apply_url: str = ""
    description: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        self.company = clean_company(self.company)
        self.title = clean_title(self.title)

    def as_fields(self) -> dict:
        return {"title": self.title, "company": self.company, "location": self.location,
                "salary": self.salary, "posted": self.posted, "source": self.source,
                "easy_apply": int(self.easy_apply), "apply_url": self.apply_url,
                "description": self.description}


BROWSER_SOURCES = ("indeed", "glassdoor")
ATS_SOURCES = ("greenhouse", "lever", "ashby")
