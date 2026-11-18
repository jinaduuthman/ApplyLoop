"""Company boards via public ATS JSON APIs (Greenhouse, Lever, Ashby) — no browser needed."""

from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timezone

from . import JobPosting

UA = {"User-Agent": "applyloop/1.0 (+personal job search)"}


def _get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _strip_html(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", s)).strip()


def _age_days(iso_or_ms) -> float | None:
    try:
        if isinstance(iso_or_ms, (int, float)):
            dt = datetime.fromtimestamp(iso_or_ms / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(iso_or_ms).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except Exception:
        return None


def greenhouse(slug: str, company: str) -> list[JobPosting]:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    return [JobPosting(url=j["absolute_url"], title=j.get("title", ""), company=company,
                       location=(j.get("location") or {}).get("name", ""),
                       posted=j.get("updated_at", ""), source="greenhouse",
                       apply_url=greenhouse_form(slug, j["id"]),
                       description=_strip_html(j.get("content", "")))
            for j in data.get("jobs", [])]


def greenhouse_form(slug: str, job_id) -> str:
    """Greenhouse's embeddable application form — the form itself, without the company's
    marketing page around it (which often loads the form late or in an iframe)."""
    return f"https://boards.greenhouse.io/embed/job_app?for={slug}&token={job_id}"


def lever(slug: str, company: str) -> list[JobPosting]:
    data = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        sal = j.get("salaryRange") or {}
        salary = f"${sal.get('min', '')}-${sal.get('max', '')}" if sal.get("max") else ""
        out.append(JobPosting(url=j["hostedUrl"], title=j.get("text", ""), company=company,
                              location=cats.get("location", ""), salary=salary,
                              posted=str(j.get("createdAt", "")), source="lever",
                              apply_url=j.get("applyUrl") or j["hostedUrl"],
                              description=j.get("descriptionPlain", "") + "\n\n" +
                              "\n".join(_strip_html(l.get("content", "")) for l in j.get("lists", []))))
    return out


def ashby(slug: str, company: str) -> list[JobPosting]:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        comp = (j.get("compensation") or {}).get("compensationTierSummary", "")
        out.append(JobPosting(url=j["jobUrl"], title=j.get("title", ""), company=company,
                              location=j.get("location", ""), salary=comp,
                              posted=j.get("publishedAt", ""), source="ashby",
                              apply_url=j.get("applyUrl") or j["jobUrl"],
                              description=j.get("descriptionPlain", "")))
    return out


def _post(url: str, payload: dict, timeout: int = 30):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={**UA, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def workday_parts(url: str) -> tuple[str, str, str]:
    """https://homedepot.wd5.myworkdayjobs.com/en-US/CareerDepot → (base, tenant, site)."""
    from urllib.parse import urlparse
    u = urlparse(url if "://" in url else "https://" + url)
    segs = [x for x in u.path.split("/") if x and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)]
    if not u.netloc or not segs:
        raise ValueError("workday url should look like https://<tenant>.wd5.myworkdayjobs.com/<Site>")
    return f"{u.scheme}://{u.netloc}", u.netloc.split(".")[0], segs[0]


def _wd_age(posted_on: str) -> float:
    t = (posted_on or "").lower()
    if "today" in t:
        return 0
    if "yesterday" in t:
        return 1
    m = re.search(r"(\d+)\+?\s*day", t)
    return float(m.group(1)) if m else 0


def workday(url: str, company: str, queries: list[str] | None = None, max_age_days: int = 0,
            title_ok=None, per_query: int = 200, max_details: int = 80) -> list[JobPosting]:
    """Workday career sites (Home Depot, Mastercard, …) through the same public JSON endpoints
    their careers page uses. Searches by your track queries; details only for relevant titles.
    Applying on Workday needs your account with that employer, so these are hand-offs."""
    base, tenant, site = workday_parts(url)
    api = f"{base}/wday/cxs/{tenant}/{site}"
    hits: dict[str, dict] = {}
    page_size = 20                                   # Workday's maximum per request
    for q in queries or [""]:
        offset, total = 0, None
        while offset < per_query:                    # walk every results page (up to per_query)
            data = _post(f"{api}/jobs", {"appliedFacets": {}, "limit": page_size, "offset": offset,
                                         "searchText": q})
            posts = data.get("jobPostings") or []
            if total is None:
                total = int(data.get("total") or 0)  # only the first page reports the total
            for j in posts:
                path = j.get("externalPath")
                if not path or path in hits:
                    continue
                if max_age_days and _wd_age(j.get("postedOn", "")) > max_age_days:
                    continue
                if title_ok and not title_ok(j.get("title", "")):
                    continue
                hits[path] = j
            offset += page_size
            if len(posts) < page_size or (total and offset >= total):
                break
    out = []
    for path, j in list(hits.items())[:max_details]:
        desc, loc, posted = "", j.get("locationsText", ""), ""
        try:
            info = _get(f"{api}{path}").get("jobPostingInfo") or {}
            desc = _strip_html(info.get("jobDescription", ""))
            loc = info.get("location") or loc
            posted = info.get("startDate", "")
            if info.get("remoteType"):
                loc = f"{loc} ({info['remoteType']})"
        except Exception:
            pass
        page = f"{base}/{site}{path}"
        out.append(JobPosting(url=page, title=j.get("title", ""), company=company, location=loc,
                              posted=posted, source="workday", apply_url=page + "/apply",
                              description=desc))
    return out


def smartrecruiters(slug: str, company: str) -> list[JobPosting]:
    data = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    out = []
    for j in data.get("content") or []:
        loc = j.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
        if loc.get("remote"):
            where = f"{where} (Remote)" if where else "Remote"
        url = f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}"
        desc = ""
        try:
            ad = (_get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j.get('id')}")
                  .get("jobAd") or {}).get("sections") or {}
            desc = _strip_html(" ".join((ad.get(k) or {}).get("text", "") for k in
                                        ("jobDescription", "qualifications", "additionalInformation")))
        except Exception:
            pass
        out.append(JobPosting(url=url, title=j.get("name", ""), company=company, location=where,
                              posted=j.get("releasedDate", ""), source="smartrecruiters",
                              apply_url=url, description=desc))
    return out


def recruitee(slug: str, company: str) -> list[JobPosting]:
    data = _get(f"https://{slug}.recruitee.com/api/offers/")
    return [JobPosting(url=o.get("careers_url", ""), title=o.get("title", ""), company=company,
                       location=o.get("location", "") + (" (Remote)" if o.get("remote") else ""),
                       posted=o.get("published_at", "") or o.get("created_at", ""), source="recruitee",
                       apply_url=o.get("careers_apply_url") or o.get("careers_url", ""),
                       description=_strip_html((o.get("description") or "") + " " + (o.get("requirements") or "")))
            for o in data.get("offers") or [] if o.get("careers_url")]


def workable(slug: str, company: str) -> list[JobPosting]:
    data = _post(f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
                 {"query": "", "location": [], "department": [], "worktype": [], "remote": []})
    out = []
    for j in data.get("results") or []:
        sc = j.get("shortcode")
        if not sc:
            continue
        loc = j.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
        if j.get("remote"):
            where = f"{where} (Remote)" if where else "Remote"
        url = f"https://apply.workable.com/{slug}/j/{sc}/"
        out.append(JobPosting(url=url, title=j.get("title", ""), company=company, location=where,
                              posted=j.get("published", ""), source="workable", apply_url=url + "apply/"))
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
            "smartrecruiters": smartrecruiters, "recruitee": recruitee, "workable": workable}


def fetch_company(cfg: dict, max_age_days: int = 0, queries: list[str] | None = None,
                  title_ok=None) -> list[JobPosting]:
    ats = str(cfg.get("ats", "")).lower()
    if ats == "workday":
        if not cfg.get("url"):
            return []
        return workday(cfg["url"], cfg.get("name", ""), queries, max_age_days, title_ok)
    fn = FETCHERS.get(ats)
    if not fn or not cfg.get("slug"):
        return []
    jobs = fn(cfg["slug"], cfg.get("name", cfg["slug"]))
    if max_age_days:
        jobs = [j for j in jobs
                if (_age_days(int(j.posted) if j.posted.isdigit() else j.posted) or 0) <= max_age_days]
    return jobs


_BOARD_CACHE: dict[str, list[JobPosting]] = {}


def slug_candidates(company: str) -> list[str]:
    """Likely public board slugs for a company name ("Scale AI, Inc." → scaleai, scale-ai)."""
    name = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|the|technologies|labs)\b\.?", " ",
                  (company or "").lower())
    words = re.findall(r"[a-z0-9]+", name)
    if not words:
        return []
    out = ["".join(words), "-".join(words)]
    return list(dict.fromkeys(s for s in out if len(s) >= 3))


def find_board(company: str) -> list[JobPosting]:
    """Postings from the company's own Greenhouse/Lever/Ashby board, found by guessing its public
    slug from the name. Only public, documented job-board APIs; [] when none matches."""
    key = (company or "").lower().strip()
    if key in _BOARD_CACHE:
        return _BOARD_CACHE[key]
    found: list[JobPosting] = []
    for slug in slug_candidates(company):
        for ats, fn in FETCHERS.items():
            try:
                found = fn(slug, company)
            except Exception:
                found = []
            if found:
                break
        if found:
            break
    _BOARD_CACHE[key] = found
    return found
