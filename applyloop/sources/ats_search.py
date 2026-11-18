"""Find employers hiring for your titles on the big applicant-tracking systems, the way you'd
google `site:boards.greenhouse.io "Data Engineer"` — but through a search API (automated Google
searches aren't allowed). Every Greenhouse / Lever / Ashby / Workable board found this way is
then read through its public job API, like the companies in your config, so postings arrive with
dates and full descriptions and can be auto-applied.

Search API (set one key in .env):
  BRAVE_API_KEY    https://brave.com/search/api   (free plan: ~2,000 queries/month)
  SERPAPI_KEY      https://serpapi.com             (Google results; free plan ~100/month)

Boards found are remembered in data/discovered_companies.json and checked again on later runs
for `remember_days`, without spending searches.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import JobPosting

SITES = {
    "greenhouse": ["boards.greenhouse.io", "job-boards.greenhouse.io"],
    "lever": ["jobs.lever.co"],
    "ashby": ["jobs.ashbyhq.com"],
    "workable": ["apply.workable.com"],
    "yc": ["ycombinator.com/companies"],
}
_BOARD_URL = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.%-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9_-]+)", re.I)),
]
_NOT_SLUG = {"embed", "jobs", "j", "api", "v1", "boards", "careers", "search", "en", "en-us"}
# Boards that are job sites / reposting services, not employers (their listings are other
# companies' jobs, often stale or agency-run).
NOT_EMPLOYERS = {"jobgether", "canadaboard", "remotehunter", "jobot", "dice", "hired", "toptal",
                 "crossover", "turing", "andela", "braintrust", "mercor", "outlier", "dataannotation"}


def provider() -> str:
    if os.environ.get("BRAVE_API_KEY"):
        return "brave"
    if os.environ.get("SERPAPI_KEY"):
        return "serpapi"
    return ""


def _get_json(url: str, headers: dict | None = None, timeout: int = 30):
    req = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def web_search(q: str, days: int = 7, count: int = 20) -> list[dict]:
    """[{url, title, snippet}] for one query, newest results within ~`days`."""
    p = provider()
    if p == "brave":
        fresh = "pd" if days <= 1 else "pw" if days <= 7 else "pm" if days <= 31 else "py"
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
            {"q": q, "count": min(count, 20), "freshness": fresh, "country": "us"})
        data = _get_json(url, {"X-Subscription-Token": os.environ["BRAVE_API_KEY"]})
        time.sleep(1.1)                                   # free plan: 1 request / second
        return [{"url": r.get("url", ""), "title": r.get("title", ""), "snippet": r.get("description", "")}
                for r in (data.get("web") or {}).get("results", [])]
    if p == "serpapi":
        tbs = "qdr:d" if days <= 1 else "qdr:w" if days <= 7 else "qdr:m"
        url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(
            {"engine": "google", "q": q, "num": count, "tbs": tbs, "api_key": os.environ["SERPAPI_KEY"]})
        data = _get_json(url)
        return [{"url": r.get("link", ""), "title": r.get("title", ""), "snippet": r.get("snippet", "")}
                for r in data.get("organic_results", [])]
    raise RuntimeError("no search API key — add BRAVE_API_KEY (or SERPAPI_KEY) to .env")


def board_of(url: str) -> tuple[str, str] | None:
    """Job URL → (ats, slug), e.g. https://jobs.lever.co/plaid/abc → ('lever', 'plaid')."""
    for ats, rx in _BOARD_URL:
        m = rx.search(url or "")
        if m:
            slug = urllib.parse.unquote(m.group(1)).strip("/").lower()
            if slug and slug not in _NOT_SLUG:
                return ats, slug
    return None


def company_from_result(ats: str, title: str, slug: str) -> str:
    """Company name from the search result title, else from the board slug:
    Greenhouse 'Job Application for Data Engineer at Acme', Lever 'Acme - Data Engineer',
    Ashby 'Data Engineer @ Acme', Workable 'Data Engineer - Acme'."""
    t = (title or "").strip()
    pat = {"greenhouse": r"\bat\s+(.+?)\s*$", "lever": r"^(.+?)\s+[-–]\s+",
           "ashby": r"@\s*(.+?)\s*$", "workable": r".*[-–]\s+(.+?)\s*$"}.get(ats)
    m = re.search(pat, t) if pat else None
    name = m.group(1).strip(" |-–") if m else ""
    return name if 1 < len(name) <= 60 else re.sub(r"[-_]+", " ", slug).title()


def _yc_posting(r: dict) -> JobPosting | None:
    m = re.search(r"ycombinator\.com/companies/([a-z0-9-]+)/jobs/[A-Za-z0-9]+", r["url"])
    if not m:
        return None
    title = re.sub(r"\s*[|–-]\s*Y Combinator.*$", "", r["title"]).strip()
    comp = m.group(1).replace("-", " ").title()
    tm = re.match(r"(.+?)\s+at\s+(.+)$", title)
    if tm:
        title, comp = tm.group(1), tm.group(2)
    return JobPosting(url=r["url"], title=title, company=comp, source="yc", description=r.get("snippet", ""),
                      apply_url=r["url"])


# ── remembered boards ────────────────────────────────────────────────────
def _memory_path(root: Path) -> Path:
    return Path(root) / "data" / "discovered_companies.json"


def load_remembered(root: Path, days: int) -> list[dict]:
    p = _memory_path(root)
    if not p.exists() or not days:
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return []
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    return [r for r in rows if r.get("last_seen", "") >= cutoff]


def remember(root: Path, boards: list[dict]) -> None:
    p = _memory_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        rows = {(r["ats"], r["slug"]): r for r in json.loads(p.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        rows = {}
    now = datetime.now().isoformat(timespec="seconds")
    for b in boards:
        rows[(b["ats"], b["slug"])] = {**rows.get((b["ats"], b["slug"]), {}), **b, "last_seen": now}
    p.write_text(json.dumps(sorted(rows.values(), key=lambda r: r["slug"]), indent=1), encoding="utf-8")


# ── main entry ───────────────────────────────────────────────────────────
def discover(queries: list[str], days: int = 7, sites: list[str] | None = None,
             max_queries: int = 6, known: set[tuple[str, str]] | None = None,
             log=print) -> tuple[list[dict], list[JobPosting]]:
    """Search each ATS site for each title. Returns (boards to read, direct postings like YC)."""
    sites = sites or ["greenhouse", "lever", "ashby", "workable"]
    known = known or set()
    boards: dict[tuple[str, str], dict] = {}
    direct: list[JobPosting] = []
    for q in queries[:max_queries]:
        for site in sites:
            for host in SITES.get(site, [site]):        # one search per host: plain `site:` works everywhere
                try:
                    results = web_search(f'site:{host} "{q}"', days)
                except Exception as e:
                    log(f"search failed ({host}, “{q}”): {e}")
                    continue
                for r in results:
                    if site == "yc":
                        j = _yc_posting(r)
                        if j:
                            direct.append(j)
                        continue
                    b = board_of(r["url"])
                    if b and b[1] in NOT_EMPLOYERS:
                        continue
                    if b and b not in known and b not in boards:
                        boards[b] = {"ats": b[0], "slug": b[1], "name": company_from_result(b[0], r["title"], b[1])}
    return list(boards.values()), direct
