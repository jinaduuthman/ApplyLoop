"""Indeed search + job-detail extraction (logged-in browser session recommended).

Selectors are kept in one place (SEL) and parsing runs as in-page JS that tries
several fallbacks, so a markup tweak on Indeed usually needs a one-line fix here.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from ..browser import page_text, wait_for_human_if_blocked
from . import JobPosting

BASE = "https://www.indeed.com"

SEL = {
    "card": "div.job_seen_beacon, td.resultContent, li div.cardOutline",
    "description": "#jobDescriptionText, [data-testid='jobsearch-JobComponent-description'], .jobsearch-JobComponent-description",
    "easy_apply_btn": "#indeedApplyButton, button[id*='indeedApply'], [data-testid='indeedApplyButton'], "
                      "button:has-text('Apply now'), button:has-text('Easily apply')",
    "company_site_btn": "#applyButtonLinkContainer a, a[aria-label*='company site' i], "
                        "button[aria-label*='company site' i], a:has-text('Apply on company site'), "
                        "button:has-text('Apply on company site')",
}

CARDS_JS = """
() => {
  const out = [];
  const cards = document.querySelectorAll(%r);
  for (const c of cards) {
    const a = c.querySelector('a[data-jk], h2.jobTitle a, a.jcs-JobTitle');
    if (!a) continue;
    const jk = a.getAttribute('data-jk') || (a.href.match(/jk=([\\w]+)/) || [])[1] || '';
    if (!jk) continue;
    const q = s => { const e = c.querySelector(s); return e ? e.innerText.trim() : ''; };
    const text = c.innerText || '';
    out.push({
      jk,
      title: (a.querySelector('span[title]') || a).innerText.trim(),
      company: q("[data-testid='company-name'], .companyName, span.css-1h7lukg"),
      location: q("[data-testid='text-location'], .companyLocation"),
      salary: q("[data-testid='attribute_snippet_testid'], .salary-snippet-container, .estimated-salary"),
      posted: q("[data-testid='myJobsStateDate'], span.date"),
      easy: /easily apply|indeed apply/i.test(text) || !!c.querySelector('[data-testid="indeedApply"], .iaLabel'),
    });
  }
  return out;
}
""" % SEL["card"]


REMOTE_PARAM = "remotejob=032b3046-06a3-4876-8dfd-474eb5e7ed11"   # Indeed's "Remote" filter


def search_url(query: str, location: str, days: int, start: int = 0, remote: bool = False) -> str:
    url = f"{BASE}/jobs?q={quote_plus(query)}&l={quote_plus(location)}&sort=date"
    if remote:
        url += "&" + REMOTE_PARAM
    if days:
        url += f"&fromage={days}"
    if start:
        url += f"&start={start}"
    return url


def search(ctx, query: str, location: str, days: int = 7, max_results: int = 30,
           interactive: bool = True, remote: bool = False) -> list[JobPosting]:
    page = ctx.new_page()
    jobs: list[JobPosting] = []
    seen: set[str] = set()
    try:
        start = 0
        while len(jobs) < max_results and start <= 90:
            page.goto(search_url(query, location, days, start, remote), wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            if not wait_for_human_if_blocked(page, interactive):
                break
            rows = page.evaluate(CARDS_JS) or []
            fresh = [r for r in rows if r["jk"] not in seen]
            if not fresh:
                break
            for r in fresh:
                seen.add(r["jk"])
                jobs.append(JobPosting(
                    url=f"{BASE}/viewjob?jk={r['jk']}", title=r["title"], company=r["company"],
                    location=r["location"], salary=r["salary"], posted=r["posted"],
                    source="indeed", easy_apply=bool(r["easy"])))
            start += 10
            page.wait_for_timeout(1500)
    finally:
        page.close()
    return jobs[:max_results]


def fetch_details(ctx, job: JobPosting, interactive: bool = True) -> JobPosting:
    page = ctx.new_page()
    try:
        page.goto(job.url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        if not wait_for_human_if_blocked(page, interactive):
            return job
        el = page.query_selector(SEL["description"])
        job.description = (el.inner_text() if el else page_text(page)).strip()
        if page.query_selector(SEL["easy_apply_btn"]):
            job.easy_apply = True
        link = page.query_selector(SEL["company_site_btn"])
        if link:
            href = link.get_attribute("href") or ""
            if href:
                job.apply_url = href if href.startswith("http") else BASE + href
            if not page.query_selector(SEL["easy_apply_btn"]):
                job.easy_apply = False
        if any(s in job.description.lower()[:400] for s in ("this job has expired", "no longer available")):
            job.extra["expired"] = True
    finally:
        page.close()
    return job
