"""Teal (tealhq.com) public job board — no login, no API key.

Listing pages: https://www.tealhq.com/jobs/title/<slug>?page=N  (20 jobs per page, newest first)
Each card links to a Teal job page (tealhq.com/job/<slug>_<id>) with the full description; the
real application lives on the employer's site, so applyloop treats Teal like other job-listing
sites and looks the job up on the employer's own careers page before applying.

The page layout isn't a documented API: parsing tries the embedded Next.js data first, then the
visible cards. `applyloop teal test "Data Engineer" --dump` saves the raw page if it ever breaks.
"""

from __future__ import annotations

import html as _html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from . import JobPosting

BASE = "https://www.tealhq.com"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15 applyloop",
      "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}

_AGE = re.compile(r"^(\d+)\s*(m|min|mins|h|hr|hrs|d|w|mo)\+?(\s*ago)?$|^(just posted|today|new job|new)$", re.I)
_SALARY = re.compile(r"\$\s?\d[\d,.]*\s*[kK]?(\s*[-–]\s*\$?\s?\d[\d,.]*\s*[kK]?)?(\s*(/|per)\s*(yr|year|hr|hour))?")
_LOCATION = re.compile(r"\b(remote|hybrid|on-?site|[A-Z][a-z]+(?: [A-Z][a-z]+)*, [A-Z]{2}\b|united states)", re.I)
_NOISE = re.compile(r"^(new job|save|apply|match resume|easy apply|full-?time|part-?time|contract|"
                    r"entry level|mid level|senior level|senior|mid-?senior|\W+)$", re.I)


def slug(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (query or "").lower()).strip("-")


def _get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _abs(href: str) -> str:
    return urllib.parse.urljoin(BASE, _html.unescape(href))


# ── parsing ───────────────────────────────────────────────────────────────
def _from_next_data(page: str) -> list[JobPosting]:
    """Next.js pages embed their data as JSON: find dicts that look like job cards."""
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    out: list[JobPosting] = []

    def pick(d: dict, *keys):
        for k in keys:
            v = d.get(k)
            if isinstance(v, dict):
                v = v.get("name") or v.get("displayName") or v.get("title")
            if v:
                return str(v)
        return ""

    def walk(x):
        if isinstance(x, dict):
            title = pick(x, "title", "jobTitle", "name")
            comp = pick(x, "company", "companyName", "organization", "employer")
            link = pick(x, "url", "jobUrl", "href", "path", "slug")
            if title and comp and link and ("job" in link or "_" in link):
                if not link.startswith("http"):
                    link = _abs(link if link.startswith("/") else f"/job/{link}")
                out.append(JobPosting(
                    url=link, title=title, company=comp,
                    location=pick(x, "location", "locationName", "city", "formattedLocation"),
                    salary=pick(x, "salary", "salaryRange", "compensation"),
                    posted=pick(x, "postedAt", "publishedAt", "datePosted", "createdAt", "posted"),
                    description=pick(x, "description", "summary")[:6000], source="teal",
                    apply_url=pick(x, "applyUrl", "applicationUrl", "externalUrl") or link))
                return
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data)
    return out


def _from_cards(page: str) -> list[JobPosting]:
    """Visible job cards: the anchor to /job/<slug>_<id> is the title; the short lines after
    it are company, location, salary and age ('11h', '1d')."""
    s = re.sub(r"(?is)<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", page)
    anchors = list(re.finditer(r'(?is)<a\b[^>]*href=["\'](/job/[^"\'#?]+|https://www\.tealhq\.com/job/[^"\'#?]+)["\'][^>]*>(.*?)</a>', s))
    out: list[JobPosting] = []
    seen: set[str] = set()
    for i, m in enumerate(anchors):
        url = _abs(m.group(1))
        if url in seen:
            continue
        seen.add(url)
        end = anchors[i + 1].start() if i + 1 < len(anchors) else min(len(s), m.end() + 4000)
        chunk = m.group(2) + "\n" + s[m.end():end]
        chunk = re.sub(r"(?i)<(br|/p|/div|/li|/span|/h\d|/a)[^>]*>", "\n", chunk)
        lines = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", ln))).strip()
                 for ln in chunk.split("\n")]
        parts = []
        for ln in lines:                                  # "3d • Remote" → "3d", "Remote"
            parts += [x.strip(" ,;|") for x in re.split(r"\s*[•·|]\s*", ln)]
        lines = [ln for ln in parts if ln and not _NOISE.match(ln)]
        if len(lines) < 2:
            continue
        title, rest = lines[0], lines[1:12]
        posted = next((ln for ln in rest if _AGE.match(ln)), "")
        salary = next((ln for ln in rest if _SALARY.search(ln)), "")
        location = next((ln for ln in rest if ln not in (posted, salary) and _LOCATION.search(ln)), "")
        company = next((ln for ln in rest if ln not in (posted, salary, location) and len(ln) <= 80), "")
        company = company.strip(" ,;•·|")
        if not company:
            continue
        out.append(JobPosting(url=url, title=title, company=company, location=location, salary=salary,
                              posted=posted, source="teal", apply_url=url))
    return out


def parse_listing(page: str) -> list[JobPosting]:
    jobs = _from_next_data(page) or _from_cards(page)
    uniq: dict[str, JobPosting] = {}
    for j in jobs:
        uniq.setdefault(j.url, j)
    return list(uniq.values())


# ── search ────────────────────────────────────────────────────────────────
def search(query: str, days: int = 7, max_results: int = 40, remote: bool = False,
           dump_dir: str | Path | None = None) -> list[JobPosting]:
    """Newest Teal postings for a job title (e.g. "Data Engineer"). Older than `days` stops paging."""
    from ..filters import parse_posted_days
    out: list[JobPosting] = []
    for page_no in range(1, 4):
        url = f"{BASE}/jobs/title/{slug(query)}" + (f"?page={page_no}" if page_no > 1 else "")
        page = _get(url)
        if dump_dir:
            d = Path(dump_dir).expanduser()
            d.mkdir(parents=True, exist_ok=True)
            (d / f"teal-{slug(query)}-{page_no}.html").write_text(page, encoding="utf-8")
        jobs = parse_listing(page)
        if not jobs:
            break
        fresh = [j for j in jobs if (parse_posted_days(j.posted) or 0) <= (days or 9999)]
        if remote:
            fresh = [j for j in fresh if re.search(r"\bremote\b", f"{j.location} {j.title}", re.I)]
        out += fresh
        if len(out) >= max_results or len(fresh) < len(jobs) // 2:      # past the date window
            break
    return out[:max_results]
