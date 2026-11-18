"""Glassdoor search + detail extraction. Glassdoor's Easy Apply hands off to Indeed's
apply flow, which the apply engine handles the same way as Indeed."""

from __future__ import annotations

from urllib.parse import quote_plus

from ..browser import page_text, wait_for_human_if_blocked
from . import JobPosting

BASE = "https://www.glassdoor.com"

CARDS_JS = """
() => {
  const out = [];
  const cards = document.querySelectorAll("li[data-test='jobListing'], li[data-jobid], div[data-test='job-card']");
  for (const c of cards) {
    const a = c.querySelector("a[data-test='job-title'], a[data-test='job-link'], a[class*='jobTitle'], a[href*='job-listing']");
    if (!a) continue;
    const q = s => { const e = c.querySelector(s); return e ? e.innerText.trim() : ''; };
    const id = c.getAttribute('data-jobid') || c.getAttribute('data-id') ||
               (a.href.match(/jobListingId=(\\d+)/) || a.href.match(/(\\d{9,})/) || [])[1] || '';
    out.push({
      id, href: a.href, title: a.innerText.trim(),
      company: q("[class*='EmployerProfile_compactEmployerName'], [class*='employerName'], [data-test='employer-name'], div[class*='EmployerProfile'] span"),
      location: q("[data-test='emp-location'], [class*='JobCard_location']"),
      salary: q("[data-test='detailSalary'], [class*='salaryEstimate']"),
      posted: q("[data-test='job-age'], [class*='listingAge']"),
      easy: /easy apply/i.test(c.innerText || ''),
    });
  }
  return out;
}
"""

SEL = {
    "description": "[class*='JobDetails_jobDescription'], [data-test='jobDescriptionContent'], #JobDescriptionContainer",
    "easy_apply_btn": "button[data-test='easyApply'], button:has-text('Easy Apply')",
    "employer_site_btn": "[data-test='applyButton'], a:has-text('Apply on employer site'), button:has-text('Apply on employer site')",
    "modal_close": "button[aria-label='Close'], [alt='Close'], button.CloseButton, .modal_closeIcon",
}


def search_url(query: str, location: str, days: int, remote: bool = False) -> str:
    url = f"{BASE}/Job/jobs.htm?sc.keyword={quote_plus(query)}&locKeyword={quote_plus(location)}&sortBy=date_desc"
    if remote:
        url += "&remoteWorkType=1"
    if days:
        url += f"&fromAge={days}"
    return url


def _dismiss_modal(page) -> None:
    try:
        btn = page.query_selector(SEL["modal_close"])
        if btn and btn.is_visible():
            btn.click()
        else:
            page.keyboard.press("Escape")
    except Exception:
        pass


def search(ctx, query: str, location: str, days: int = 7, max_results: int = 30,
           interactive: bool = True, remote: bool = False) -> list[JobPosting]:
    page = ctx.new_page()
    jobs: list[JobPosting] = []
    try:
        page.goto(search_url(query, location, days, remote), wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        if not wait_for_human_if_blocked(page, interactive):
            return []
        _dismiss_modal(page)
        for _ in range(4):                       # "Show more jobs" pagination
            rows = page.evaluate(CARDS_JS) or []
            if len(rows) >= max_results:
                break
            more = page.query_selector("button[data-test='load-more'], button:has-text('Show more jobs')")
            if not more:
                break
            more.click()
            page.wait_for_timeout(2500)
            _dismiss_modal(page)
        rows = page.evaluate(CARDS_JS) or []
        seen = set()
        for r in rows:
            key = r["id"] or r["href"]
            if key in seen:
                continue
            seen.add(key)
            url = r["href"]
            if r["id"] and "jl=" not in url:
                url += ("&" if "?" in url else "?") + f"jl={r['id']}"   # stable id for dedup
            jobs.append(JobPosting(url=url, title=r["title"], company=r["company"],
                                   location=r["location"], salary=r["salary"], posted=r["posted"],
                                   source="glassdoor", easy_apply=bool(r["easy"]),
                                   extra={"href": r["href"]}))
    finally:
        page.close()
    return jobs[:max_results]


def fetch_details(ctx, job: JobPosting, interactive: bool = True) -> JobPosting:
    page = ctx.new_page()
    try:
        page.goto(job.extra.get("href") or job.url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        if not wait_for_human_if_blocked(page, interactive):
            return job
        _dismiss_modal(page)
        el = page.query_selector(SEL["description"])
        job.description = (el.inner_text() if el else page_text(page)).strip()
        if page.query_selector(SEL["easy_apply_btn"]):
            job.easy_apply = True
        else:
            link = page.query_selector(SEL["employer_site_btn"])
            if link:
                job.easy_apply = False
                href = link.get_attribute("href") or ""
                if href:
                    job.apply_url = href if href.startswith("http") else BASE + href
    finally:
        page.close()
    return job
