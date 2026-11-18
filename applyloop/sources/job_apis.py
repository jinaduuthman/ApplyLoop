"""Job-search APIs that allow programmatic access — no browser, no bot checks.

  adzuna    Large US aggregator (many postings that also appear on Indeed/Glassdoor/company sites).
            Free key: https://developer.adzuna.com → put ADZUNA_APP_ID / ADZUNA_APP_KEY in .env.
            Descriptions are snippets; the agent fetches the full posting when it can.
  remotive  Remote-only tech jobs, public API (https://remotive.com/api/remote-jobs), no key.
            Please keep volume low (a few calls a day) and credit Remotive as the source.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import JobPosting

UA = {"User-Agent": "applyloop/1.1 (personal job search)"}
API_SOURCES = ("adzuna", "remotive", "teal")


def _get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _text(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def adzuna_configured() -> bool:
    return bool(os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))


def adzuna(query: str, location: str = "", days: int = 7, max_results: int = 50,
           country: str = "us", remote: bool = False) -> list[JobPosting]:
    if not adzuna_configured():
        raise RuntimeError("Adzuna keys missing — add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env "
                           "(free at developer.adzuna.com)")
    out: list[JobPosting] = []
    what = f"{query} remote" if remote else query
    where = "" if (remote or location.lower() == "remote") else location
    for page in range(1, 4):
        params = {"app_id": os.environ["ADZUNA_APP_ID"], "app_key": os.environ["ADZUNA_APP_KEY"],
                  "what": what, "results_per_page": min(50, max_results), "sort_by": "date",
                  "content-type": "application/json"}
        if where:
            params["where"] = where
        if days:
            params["max_days_old"] = days
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}?" + urllib.parse.urlencode(params)
        data = _get(url)
        rows = data.get("results", [])
        for j in rows:
            lo, hi = j.get("salary_min"), j.get("salary_max")
            salary = f"${int(lo):,} - ${int(hi):,} a year" if lo and hi and not j.get("salary_is_predicted") == "1" else ""
            out.append(JobPosting(
                url=j.get("redirect_url", ""), title=_text(j.get("title", "")),
                company=(j.get("company") or {}).get("display_name", ""),
                location=(j.get("location") or {}).get("display_name", ""),
                salary=salary, posted=j.get("created", ""), source="adzuna",
                apply_url=j.get("redirect_url", ""), description=_text(j.get("description", "")),
                extra={"snippet": True}))
        if len(rows) < params["results_per_page"] or len(out) >= max_results:
            break
    return out[:max_results]


def remotive(query: str, days: int = 7, max_results: int = 50) -> list[JobPosting]:
    url = "https://remotive.com/api/remote-jobs?" + urllib.parse.urlencode(
        {"search": query, "limit": max_results})
    data = _get(url)
    out = []
    now = datetime.now(timezone.utc)
    for j in data.get("jobs", []):
        posted = j.get("publication_date", "")
        try:
            age = (now - datetime.fromisoformat(posted.replace("Z", "+00:00")).replace(
                tzinfo=timezone.utc)).days
            if days and age > days:
                continue
        except ValueError:
            pass
        loc = j.get("candidate_required_location") or "Remote"
        out.append(JobPosting(
            url=j.get("url", ""), title=j.get("title", ""), company=j.get("company_name", ""),
            location=f"Remote ({loc})" if "remote" not in loc.lower() else loc,
            salary=j.get("salary", "") or "", posted=posted, source="remotive",
            apply_url=j.get("url", ""), description=_text(j.get("description", ""))))
    return out[:max_results]
