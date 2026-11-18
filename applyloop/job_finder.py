"""Automated job discovery — searches boards, portals, and career pages.

Supports two backends:
  - **Stagehand** (AI-powered): Uses natural language to extract job listings
    from any site. Enable with STAGEHAND_ENABLED=true in .env.
  - **Playwright** (selector-based): Traditional CSS-selector scraping per site.
    Used as the default fallback.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

from rich.console import Console
from rich.table import Table

from .config import ProjectPaths, load_profile, load_portals
from .scanner import ScannedJob, _load_scan_history, _save_scan_history
from .stagehand_browser import is_stagehand_enabled

console = Console()


@dataclass
class FoundJob:
    url: str
    title: str
    company: str
    location: str = ""
    source: str = ""
    posted_date: str = ""
    salary: str = ""
    description_snippet: str = ""
    is_new: bool = True
    score: float = 0.0

    def to_scanned(self) -> ScannedJob:
        return ScannedJob(
            url=self.url,
            title=self.title,
            company=self.company,
            location=self.location,
            source=self.source,
            is_new=self.is_new,
        )


def _title_matches_profile(title: str, target_roles: list[str]) -> bool:
    """Check if a job title is relevant to the candidate's target roles."""
    title_lower = title.lower()
    for role in target_roles:
        role_words = [w for w in role.lower().split() if len(w) > 3]
        matches = sum(1 for w in role_words if w in title_lower)
        if matches >= max(1, len(role_words) // 2):
            return True
    return False


def _deduplicate_jobs(jobs: list[FoundJob]) -> list[FoundJob]:
    """Remove duplicate jobs based on URL normalization."""
    from .urls import canonical_url

    seen: set[str] = set()
    unique: list[FoundJob] = []
    for job in jobs:
        normalized = (canonical_url(job.url) if job.url else f"{job.company}|{job.title}").lower()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(job)
    return unique


def _search_google_jobs(
    page, query: str, location: str = "", max_results: int = 25
) -> list[FoundJob]:
    """Search Google for job listings and extract results."""
    jobs: list[FoundJob] = []
    search_query = f"{query} jobs"
    if location:
        search_query += f" {location}"

    url = f"https://www.google.com/search?q={quote_plus(search_query)}&ibp=htl;jobs"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        job_cards = page.query_selector_all("li.iFjolb, div[data-ved] div.PwjeAc, div.gws-plugins-horizon-jobs__li-ed")
        for card in job_cards[:max_results]:
            try:
                title_el = card.query_selector("div.BjJfJf, h2, .PUpOsf")
                company_el = card.query_selector("div.vNEEBe, .nJlQNd, .vNEEBe")
                location_el = card.query_selector("div.Qk80Jf, .pwO1Jc, .Qk80Jf")

                title = title_el.inner_text().strip() if title_el else ""
                company = company_el.inner_text().strip() if company_el else ""
                loc = location_el.inner_text().strip() if location_el else ""

                if title and company:
                    jobs.append(FoundJob(
                        url="",
                        title=title,
                        company=company,
                        location=loc,
                        source="google_jobs",
                    ))
            except Exception:
                continue
    except Exception as e:
        console.print(f"[yellow]Google Jobs search failed: {e}[/yellow]")

    return jobs


def _search_indeed(
    page, query: str, location: str = "", max_results: int = 25
) -> list[FoundJob]:
    """Search Indeed for job listings."""
    jobs: list[FoundJob] = []
    params = f"q={quote_plus(query)}"
    if location:
        params += f"&l={quote_plus(location)}"

    url = f"https://www.indeed.com/jobs?{params}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        cards = page.query_selector_all("div.job_seen_beacon, div.cardOutline, .resultContent")
        for card in cards[:max_results]:
            try:
                title_el = card.query_selector("h2 a, h2 span[title], .jobTitle a")
                company_el = card.query_selector("[data-testid='company-name'], .companyName, .company")
                location_el = card.query_selector("[data-testid='text-location'], .companyLocation, .location")
                link_el = card.query_selector("h2 a, .jobTitle a, a[data-jk]")

                title = title_el.inner_text().strip() if title_el else ""
                company = company_el.inner_text().strip() if company_el else ""
                loc = location_el.inner_text().strip() if location_el else ""
                href = link_el.get_attribute("href") if link_el else ""

                if href and not href.startswith("http"):
                    href = urljoin("https://www.indeed.com", href)

                if title:
                    jobs.append(FoundJob(
                        url=href or "",
                        title=title,
                        company=company,
                        location=loc,
                        source="indeed",
                    ))
            except Exception:
                continue
    except Exception as e:
        console.print(f"[yellow]Indeed search failed: {e}[/yellow]")

    return jobs


def _search_linkedin(
    page, query: str, location: str = "", max_results: int = 25
) -> list[FoundJob]:
    """Search LinkedIn public job listings (no login required)."""
    jobs: list[FoundJob] = []
    params = f"keywords={quote_plus(query)}"
    if location:
        params += f"&location={quote_plus(location)}"

    url = f"https://www.linkedin.com/jobs/search/?{params}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        cards = page.query_selector_all("div.base-card, .job-search-card, li .base-card")
        for card in cards[:max_results]:
            try:
                title_el = card.query_selector("h3, .base-search-card__title")
                company_el = card.query_selector("h4, .base-search-card__subtitle a")
                location_el = card.query_selector(".job-search-card__location")
                link_el = card.query_selector("a.base-card__full-link, a[data-tracking-control-name]")

                title = title_el.inner_text().strip() if title_el else ""
                company = company_el.inner_text().strip() if company_el else ""
                loc = location_el.inner_text().strip() if location_el else ""
                href = link_el.get_attribute("href") if link_el else ""

                if title:
                    jobs.append(FoundJob(
                        url=href or "",
                        title=title,
                        company=company,
                        location=loc,
                        source="linkedin",
                    ))
            except Exception:
                continue
    except Exception as e:
        console.print(f"[yellow]LinkedIn search failed: {e}[/yellow]")

    return jobs


def _search_glassdoor(
    page, query: str, location: str = "", max_results: int = 25
) -> list[FoundJob]:
    """Search Glassdoor for job listings."""
    jobs: list[FoundJob] = []
    params = f"sc.keyword={quote_plus(query)}"
    if location:
        params += f"&locT=C&locKeyword={quote_plus(location)}"

    url = f"https://www.glassdoor.com/Job/jobs.htm?{params}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        cards = page.query_selector_all(
            "li[data-test='jobListing'], "
            "li.JobsList_jobListItem__wjTHv, "
            "ul.JobsList_jobsList__lqjXs > li, "
            "div.JobCard_jobCardContainer__arQlW"
        )
        for card in cards[:max_results]:
            try:
                title_el = card.query_selector(
                    "a[data-test='job-title'], "
                    "a.JobCard_jobTitle__GLyJ1, "
                    "a.jobTitle, "
                    "a[class*='jobTitle']"
                )
                company_el = card.query_selector(
                    "span.EmployerProfile_compactEmployerName__9MGcV, "
                    "div.EmployerProfile_employerInfo__GaPbq a, "
                    "span[class*='employerName'], "
                    "div.jobCard_header div:first-child"
                )
                location_el = card.query_selector(
                    "div[data-test='emp-location'], "
                    "div.JobCard_location__Ds1fM, "
                    "span[class*='location']"
                )
                salary_el = card.query_selector(
                    "div.JobCard_salaryEstimate__arV5J, "
                    "span[data-test='detailSalary'], "
                    "div[class*='salary']"
                )

                title = title_el.inner_text().strip() if title_el else ""
                company = company_el.inner_text().strip() if company_el else ""
                loc = location_el.inner_text().strip() if location_el else ""
                salary = salary_el.inner_text().strip() if salary_el else ""
                href = title_el.get_attribute("href") if title_el else ""

                if href and not href.startswith("http"):
                    href = urljoin("https://www.glassdoor.com", href)

                if title:
                    jobs.append(FoundJob(
                        url=href or "",
                        title=title,
                        company=company,
                        location=loc,
                        salary=salary,
                        source="glassdoor",
                    ))
            except Exception:
                continue
    except Exception as e:
        console.print(f"[yellow]Glassdoor search failed: {e}[/yellow]")

    return jobs


def _search_portals(
    page, paths: ProjectPaths, target_roles: list[str]
) -> list[FoundJob]:
    """Scan configured ATS portals from portals.yml."""
    from .scanner import (
        _scan_ashby,
        _scan_careers_page,
        _scan_greenhouse,
        _scan_lever,
    )

    jobs: list[FoundJob] = []
    portals_config = load_portals(paths)
    if not portals_config:
        return jobs

    tracked = portals_config.get("tracked_companies", [])
    for company_config in tracked:
        name = company_config.get("name", "Unknown")
        careers_url = company_config.get("careers_url", "")
        ats = company_config.get("ats", "").lower()
        slug = company_config.get("slug", "")

        console.print(f"[dim]  Portal: {name} ({ats or 'careers'})...[/dim]")

        scanned: list[ScannedJob] = []
        if ats == "greenhouse" and slug:
            scanned = _scan_greenhouse(page, slug, name)
        elif ats == "lever" and slug:
            scanned = _scan_lever(page, slug, name)
        elif ats == "ashby" and slug:
            scanned = _scan_ashby(page, slug, name)
        elif careers_url:
            scanned = _scan_careers_page(page, careers_url, name)

        for s in scanned:
            if target_roles and not _title_matches_profile(s.title, target_roles):
                continue
            jobs.append(FoundJob(
                url=s.url,
                title=s.title,
                company=s.company,
                location=s.location,
                source=f"portal:{s.source}",
            ))

    return jobs


# ── Stagehand-powered search (AI, no CSS selectors) ──────────────

SOURCE_URLS = {
    "indeed": "https://www.indeed.com/jobs?q={query}&l={location}",
    "linkedin": "https://www.linkedin.com/jobs/search/?keywords={query}&location={location}",
    "google_jobs": "https://www.google.com/search?q={query}+jobs+{location}&ibp=htl;jobs",
    "glassdoor": "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={query}&locKeyword={location}",
}


def _search_with_stagehand(
    browser,
    source: str,
    query: str,
    location: str = "",
    max_results: int = 25,
) -> list[FoundJob]:
    """Search any job board using Stagehand AI — no CSS selectors needed."""
    from .stagehand_browser import JOB_LISTING_SCHEMA

    url_template = SOURCE_URLS.get(source)
    if not url_template:
        console.print(f"[yellow]Unknown source for Stagehand: {source}[/yellow]")
        return []

    url = url_template.format(
        query=quote_plus(query),
        location=quote_plus(location) if location else "",
    )

    try:
        browser.navigate(url)

        data = browser.extract(
            instruction=(
                f"Extract all job listings visible on this page (up to {max_results}). "
                "For each job, get the title, company name, location, "
                "URL/link to the full posting, salary if shown, and posting date if shown. "
                "Return an empty list if no job listings are found."
            ),
            schema=JOB_LISTING_SCHEMA,
        )

        raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []

        jobs: list[FoundJob] = []
        for raw in raw_jobs[:max_results]:
            title = raw.get("title", "").strip()
            if not title:
                continue

            job_url = raw.get("url", "")
            if job_url and not job_url.startswith("http"):
                base_domain = urlparse(url).scheme + "://" + urlparse(url).hostname
                job_url = urljoin(base_domain, job_url)

            jobs.append(FoundJob(
                url=job_url or "",
                title=title,
                company=raw.get("company", "").strip(),
                location=raw.get("location", "").strip(),
                salary=raw.get("salary", "").strip(),
                posted_date=raw.get("posted_date", "").strip(),
                source=source,
            ))

        return jobs
    except Exception as e:
        console.print(f"[yellow]Stagehand search on {source} failed: {e}[/yellow]")
        return []


def _find_jobs_stagehand(
    paths: ProjectPaths,
    queries: list[str],
    location: str,
    sources: list[str],
    max_per_source: int,
    include_portals: bool,
) -> list[FoundJob]:
    """Find jobs using Stagehand AI-powered browser automation."""
    from .stagehand_browser import StagehandBrowser

    all_jobs: list[FoundJob] = []

    with StagehandBrowser(headless=True) as browser:
        for query in queries:
            console.print(f"[bold cyan]Searching: \"{query}\"[/bold cyan]")

            for source in sources:
                source_label = source.replace("_", " ").title()
                console.print(f"[dim]  {source_label} (Stagehand AI)...[/dim]")
                results = _search_with_stagehand(
                    browser, source, query, location, max_per_source,
                )
                all_jobs.extend(results)
                console.print(f"  [dim]Found {len(results)} on {source_label}[/dim]")
                time.sleep(1)

    if include_portals:
        from playwright.sync_api import sync_playwright

        console.print("\n[bold cyan]Scanning portals...[/bold cyan]")
        profile = load_profile(paths)
        target_roles = profile.target_roles if profile else []

        with sync_playwright() as p:
            pw_browser = p.chromium.launch(headless=True)
            page = pw_browser.new_page()
            portal_results = _search_portals(page, paths, target_roles)
            all_jobs.extend(portal_results)
            console.print(f"  [dim]Found {len(portal_results)} from portals[/dim]")
            pw_browser.close()

    return all_jobs


# ── Main entry point ──────────────────────────────────────────────

def find_jobs(
    paths: ProjectPaths,
    queries: list[str] | None = None,
    location: str | None = None,
    sources: list[str] | None = None,
    max_per_source: int = 25,
    include_portals: bool = True,
) -> list[FoundJob]:
    """
    Search multiple sources for job listings matching the candidate's profile.

    When STAGEHAND_ENABLED=true, uses AI-powered extraction that works on
    any site without CSS selectors. Otherwise falls back to Playwright selectors.

    Sources: indeed, linkedin, google_jobs, glassdoor, portals
    """
    from playwright.sync_api import sync_playwright

    profile = load_profile(paths)

    if not queries:
        if profile and profile.target_roles:
            queries = profile.target_roles
        else:
            console.print(
                "[red]No search queries provided and no target_roles in profile.yml[/red]"
            )
            return []

    if location is None and profile:
        location = f"{profile.city}, {profile.country}" if profile.city else profile.country

    enabled_sources = sources or ["indeed", "linkedin", "google_jobs", "glassdoor"]
    history = _load_scan_history(paths)

    console.print(f"[bold]Searching for jobs...[/bold]")
    console.print(f"  Queries: {', '.join(queries)}")
    console.print(f"  Location: {location or 'Any'}")
    console.print(f"  Sources: {', '.join(enabled_sources)}")
    if is_stagehand_enabled():
        console.print(f"  Backend: [bold green]Stagehand AI[/bold green]")
    else:
        console.print(f"  Backend: Playwright (selectors)")
    if include_portals:
        console.print(f"  + Configured portals from portals.yml")
    console.print()

    # ── Stagehand path ────────────────────────────────────────────
    if is_stagehand_enabled():
        all_jobs = _find_jobs_stagehand(
            paths, queries, location or "", enabled_sources,
            max_per_source, include_portals,
        )
    # ── Playwright path (existing selector-based approach) ────────
    else:
        all_jobs: list[FoundJob] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            for query in queries:
                console.print(f"[bold cyan]Searching: \"{query}\"[/bold cyan]")

                if "indeed" in enabled_sources:
                    console.print("[dim]  Indeed...[/dim]")
                    results = _search_indeed(page, query, location or "", max_per_source)
                    all_jobs.extend(results)
                    console.print(f"  [dim]Found {len(results)} on Indeed[/dim]")
                    time.sleep(1)

                if "linkedin" in enabled_sources:
                    console.print("[dim]  LinkedIn...[/dim]")
                    results = _search_linkedin(page, query, location or "", max_per_source)
                    all_jobs.extend(results)
                    console.print(f"  [dim]Found {len(results)} on LinkedIn[/dim]")
                    time.sleep(1)

                if "google_jobs" in enabled_sources:
                    console.print("[dim]  Google Jobs...[/dim]")
                    results = _search_google_jobs(page, query, location or "", max_per_source)
                    all_jobs.extend(results)
                    console.print(f"  [dim]Found {len(results)} on Google Jobs[/dim]")
                    time.sleep(1)

                if "glassdoor" in enabled_sources:
                    console.print("[dim]  Glassdoor...[/dim]")
                    results = _search_glassdoor(page, query, location or "", max_per_source)
                    all_jobs.extend(results)
                    console.print(f"  [dim]Found {len(results)} on Glassdoor[/dim]")
                    time.sleep(1)

            if include_portals:
                console.print("\n[bold cyan]Scanning portals...[/bold cyan]")
                target_roles = profile.target_roles if profile else []
                portal_results = _search_portals(page, paths, target_roles)
                all_jobs.extend(portal_results)
                console.print(f"  [dim]Found {len(portal_results)} from portals[/dim]")

            browser.close()

    all_jobs = _deduplicate_jobs(all_jobs)

    new_jobs = [j for j in all_jobs if j.url and j.url not in history]
    for j in new_jobs:
        j.is_new = True

    if new_jobs:
        new_urls = [j.url for j in new_jobs if j.url]
        _save_scan_history(paths, new_urls)

    if all_jobs:
        console.print(f"\n[bold green]Found {len(all_jobs)} jobs ({len(new_jobs)} new):[/bold green]\n")
        table = Table(show_header=True, header_style="bold")
        table.add_column("#", justify="right", style="cyan")
        table.add_column("Company")
        table.add_column("Title")
        table.add_column("Location")
        table.add_column("Source")
        table.add_column("New", justify="center")

        for i, job in enumerate(all_jobs, 1):
            new_marker = "[green]✓[/green]" if job.is_new else "[dim]—[/dim]"
            table.add_row(
                str(i),
                job.company[:25],
                job.title[:50],
                job.location[:25],
                job.source,
                new_marker,
            )
        console.print(table)
    else:
        console.print("[dim]No jobs found matching your criteria.[/dim]")

    return all_jobs


def save_found_jobs(
    jobs: list[FoundJob], output_path: Path
) -> None:
    """Save found jobs to a file for batch processing."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# Found Jobs\n"]
    lines.append(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"# Total: {len(jobs)} jobs\n\n")

    for job in jobs:
        if job.url:
            lines.append(f"{job.url}  # {job.company} — {job.title}\n")

    output_path.write_text("".join(lines), encoding="utf-8")
    console.print(f"[green]Saved {len(jobs)} job URLs to {output_path}[/green]")
