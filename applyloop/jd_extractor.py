"""Job description extraction from URLs via Playwright or Stagehand.

When STAGEHAND_ENABLED=true, uses AI to extract structured JD data from
any page without CSS selectors. Falls back to Playwright selectors otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from rich.console import Console

from .stagehand_browser import is_stagehand_enabled

console = Console()


@dataclass
class ExtractedJD:
    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    requirements: str = ""
    full_text: str = ""
    ats_platform: str = ""
    is_active: bool = True
    error: str = ""


# ATS platform detection patterns
ATS_PATTERNS: dict[str, list[str]] = {
    "greenhouse": ["greenhouse.io", "boards.greenhouse"],
    "lever": ["lever.co", "jobs.lever"],
    "ashby": ["ashbyhq.com", "jobs.ashby"],
    "workday": ["myworkdayjobs.com", "wd5.myworkdayjobs"],
    "workable": ["apply.workable.com"],
    "wellfound": ["wellfound.com"],
    "smartrecruiters": ["smartrecruiters.com"],
    "icims": ["icims.com"],
    "bamboohr": ["bamboohr.com"],
}

EXPIRED_PATTERNS = [
    re.compile(r"job (is )?no longer available", re.I),
    re.compile(r"job.*no longer open", re.I),
    re.compile(r"position has been filled", re.I),
    re.compile(r"this job has expired", re.I),
    re.compile(r"no longer accepting applications", re.I),
    re.compile(r"this (position|role|job) (is )?no longer", re.I),
    re.compile(r"this job (listing )?is closed", re.I),
]


def _detect_ats(url: str) -> str:
    """Detect which ATS platform hosts the job posting."""
    url_lower = url.lower()
    for platform, patterns in ATS_PATTERNS.items():
        if any(p in url_lower for p in patterns):
            return platform
    return "unknown"


def _extract_greenhouse(page) -> dict:
    """Extract structured data from Greenhouse job pages."""
    data = {}
    try:
        title_el = page.query_selector("h1.app-title, h1[class*='heading'], .job-title h1, h1")
        if title_el:
            data["title"] = title_el.inner_text().strip()

        company_el = page.query_selector(".company-name, [class*='company'], .employer-name")
        if company_el:
            data["company"] = company_el.inner_text().strip()

        location_el = page.query_selector(".location, [class*='location']")
        if location_el:
            data["location"] = location_el.inner_text().strip()

        content_el = page.query_selector("#content, .content, [class*='job-description'], main")
        if content_el:
            data["full_text"] = content_el.inner_text().strip()
    except Exception:
        pass
    return data


def _extract_lever(page) -> dict:
    """Extract structured data from Lever job pages."""
    data = {}
    try:
        title_el = page.query_selector("h2, .posting-headline h2, h1")
        if title_el:
            data["title"] = title_el.inner_text().strip()

        categories = page.query_selector_all(".posting-categories .sort-by-commitment, .posting-categories .sort-by-location, .posting-categories .sort-by-team")
        if categories:
            data["location"] = " / ".join(c.inner_text().strip() for c in categories)

        content_el = page.query_selector(".posting-page, .content, main")
        if content_el:
            data["full_text"] = content_el.inner_text().strip()
    except Exception:
        pass
    return data


def _extract_generic(page) -> dict:
    """Generic extraction for unknown ATS platforms."""
    data = {}
    try:
        title_el = page.query_selector("h1")
        if title_el:
            data["title"] = title_el.inner_text().strip()

        body_text = page.evaluate("() => document.body?.innerText ?? ''")
        data["full_text"] = body_text.strip()
    except Exception:
        pass
    return data


def _extract_jd_stagehand(url: str) -> ExtractedJD:
    """Extract a job description using Stagehand AI — works on any site."""
    from .stagehand_browser import JOB_DESCRIPTION_SCHEMA, StagehandBrowser

    ats = _detect_ats(url)
    jd = ExtractedJD(url=url, ats_platform=ats)

    console.print(f"[dim]Fetching (Stagehand AI): {url}[/dim]")

    try:
        with StagehandBrowser(headless=True) as browser:
            browser.navigate(url)

            data = browser.extract(
                instruction=(
                    "Extract the full job posting from this page. "
                    "Get the job title, company name, location (city/state/remote), "
                    "the complete job description text, and the requirements/qualifications section. "
                    "Also determine if the posting appears closed, expired, or no longer accepting applications."
                ),
                schema=JOB_DESCRIPTION_SCHEMA,
            )

            if isinstance(data, dict):
                jd.title = data.get("title", "").strip()
                jd.company = data.get("company", "").strip()
                jd.location = data.get("location", "").strip()

                desc = data.get("description", "").strip()
                reqs = data.get("requirements", "").strip()
                jd.description = desc
                jd.requirements = reqs
                jd.full_text = f"{desc}\n\n{reqs}".strip() if reqs else desc

                if data.get("is_expired"):
                    jd.is_active = False
                    jd.error = "Job posting appears expired"

    except Exception as exc:
        jd.error = str(exc).split("\n")[0]
        console.print(f"[red]Stagehand extraction failed: {jd.error}[/red]")

    if not jd.company:
        domain = urlparse(url).hostname or ""
        parts = domain.replace("www.", "").split(".")
        if parts:
            jd.company = parts[0].capitalize()

    if jd.full_text:
        console.print(f"[green]Extracted: {jd.title or 'Untitled'} at {jd.company or 'Unknown'}[/green]")
        console.print(f"[dim]{len(jd.full_text)} chars of content[/dim]")

    return jd


def extract_jd_from_url(url: str) -> ExtractedJD:
    """
    Navigate to a job posting URL and extract the job description.

    When STAGEHAND_ENABLED=true, uses AI to understand the page.
    Otherwise falls back to Playwright CSS selectors.
    """
    if is_stagehand_enabled():
        return _extract_jd_stagehand(url)

    from playwright.sync_api import sync_playwright

    ats = _detect_ats(url)
    jd = ExtractedJD(url=url, ats_platform=ats)

    console.print(f"[dim]Fetching: {url}[/dim]")
    console.print(f"[dim]Platform: {ats}[/dim]")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
            http_status = response.status if response else 0

            if http_status in (404, 410):
                jd.is_active = False
                jd.error = f"HTTP {http_status}"
                browser.close()
                return jd

            page.wait_for_timeout(2500)

            body_text = page.evaluate("() => document.body?.innerText ?? ''")
            for pattern in EXPIRED_PATTERNS:
                if pattern.search(body_text):
                    jd.is_active = False
                    jd.error = "Job posting appears expired"
                    browser.close()
                    return jd

            extractors = {
                "greenhouse": _extract_greenhouse,
                "lever": _extract_lever,
            }
            extractor = extractors.get(ats, _extract_generic)
            data = extractor(page)

            jd.title = data.get("title", "")
            jd.company = data.get("company", "")
            jd.location = data.get("location", "")
            jd.full_text = data.get("full_text", "")

            if not jd.company:
                domain = urlparse(url).hostname or ""
                parts = domain.replace("www.", "").split(".")
                if parts:
                    jd.company = parts[0].capitalize()

            _split_sections(jd)

            browser.close()

    except Exception as exc:
        jd.error = str(exc).split("\n")[0]
        console.print(f"[red]Extraction failed: {jd.error}[/red]")

    if jd.full_text:
        console.print(f"[green]Extracted: {jd.title or 'Untitled'} at {jd.company or 'Unknown'}[/green]")
        console.print(f"[dim]{len(jd.full_text)} chars of content[/dim]")

    return jd


def _split_sections(jd: ExtractedJD) -> None:
    """Split full_text into description and requirements sections."""
    text = jd.full_text
    if not text:
        return

    req_headers = [
        r"(?:what we(?:'re)? look(?:ing)? for|requirements?|qualifications?|what you(?:'ll)? need|"
        r"who you are|must.have|minimum qualifications?|basic qualifications?)",
    ]

    for pattern in req_headers:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            jd.description = text[: m.start()].strip()
            jd.requirements = text[m.start() :].strip()
            return

    jd.description = text


def extract_jd_from_text(text: str, company: str = "", title: str = "") -> ExtractedJD:
    """Create an ExtractedJD from raw pasted text."""
    jd = ExtractedJD(
        url="",
        title=title,
        company=company,
        full_text=text.strip(),
    )
    _split_sections(jd)
    return jd
