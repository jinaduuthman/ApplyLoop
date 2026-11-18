"""Automated portal scanner — scans job boards and company career pages."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import ProjectPaths, load_portals

console = Console()


@dataclass
class ScannedJob:
    url: str
    title: str
    company: str
    location: str = ""
    source: str = ""
    is_new: bool = True


def _load_scan_history(paths: ProjectPaths) -> set[str]:
    """Load previously scanned URLs to avoid duplicates."""
    history_file = paths.scan_history
    if not history_file.exists():
        return set()
    seen = set()
    for line in history_file.read_text(encoding="utf-8").strip().split("\n"):
        if line and not line.startswith("#"):
            parts = line.split("\t")
            if parts:
                seen.add(parts[0].strip())
    return seen


def _save_scan_history(paths: ProjectPaths, urls: list[str]) -> None:
    """Append newly scanned URLs to history."""
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    history_file = paths.scan_history
    date_str = datetime.now().strftime("%Y-%m-%d")
    with open(history_file, "a", encoding="utf-8") as f:
        for url in urls:
            f.write(f"{url}\t{date_str}\n")


def _scan_greenhouse(page, company_slug: str, company_name: str) -> list[ScannedJob]:
    """Scan a Greenhouse board for job listings."""
    jobs: list[ScannedJob] = []
    url = f"https://boards.greenhouse.io/{company_slug}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)

        links = page.query_selector_all("a[href*='/jobs/']")
        for link in links:
            href = link.get_attribute("href") or ""
            title = link.inner_text().strip()
            if not title or not href:
                continue

            if not href.startswith("http"):
                href = f"https://boards.greenhouse.io{href}"

            location = ""
            parent = link.evaluate_handle("el => el.closest('.opening') || el.parentElement")
            if parent:
                loc_el = parent.query_selector(".location")
                if loc_el:
                    location = loc_el.inner_text().strip()

            jobs.append(ScannedJob(
                url=href,
                title=title,
                company=company_name,
                location=location,
                source="greenhouse",
            ))
    except Exception as e:
        console.print(f"[yellow]Greenhouse scan failed for {company_slug}: {e}[/yellow]")

    return jobs


def _scan_lever(page, company_slug: str, company_name: str) -> list[ScannedJob]:
    """Scan a Lever careers page for job listings."""
    jobs: list[ScannedJob] = []
    url = f"https://jobs.lever.co/{company_slug}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)

        postings = page.query_selector_all(".posting")
        for posting in postings:
            link = posting.query_selector("a.posting-title")
            if not link:
                continue
            href = link.get_attribute("href") or ""
            title_el = link.query_selector("h5")
            title = title_el.inner_text().strip() if title_el else link.inner_text().strip()

            location = ""
            loc_el = posting.query_selector(".sort-by-location, .posting-categories .location")
            if loc_el:
                location = loc_el.inner_text().strip()

            if href and title:
                jobs.append(ScannedJob(
                    url=href,
                    title=title,
                    company=company_name,
                    location=location,
                    source="lever",
                ))
    except Exception as e:
        console.print(f"[yellow]Lever scan failed for {company_slug}: {e}[/yellow]")

    return jobs


def _scan_ashby(page, company_slug: str, company_name: str) -> list[ScannedJob]:
    """Scan an Ashby careers page for job listings."""
    jobs: list[ScannedJob] = []
    url = f"https://jobs.ashbyhq.com/{company_slug}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)

        links = page.query_selector_all("a[href*='/jobs/']")
        for link in links:
            href = link.get_attribute("href") or ""
            title = link.inner_text().strip()
            if not title or not href or len(title) < 3:
                continue

            if not href.startswith("http"):
                href = f"https://jobs.ashbyhq.com{href}"

            jobs.append(ScannedJob(
                url=href,
                title=title,
                company=company_name,
                source="ashby",
            ))
    except Exception as e:
        console.print(f"[yellow]Ashby scan failed for {company_slug}: {e}[/yellow]")

    return jobs


def _scan_careers_page(page, careers_url: str, company_name: str) -> list[ScannedJob]:
    """Scan a generic company careers page."""
    jobs: list[ScannedJob] = []

    try:
        page.goto(careers_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)

        job_link_patterns = [
            "a[href*='/jobs/']",
            "a[href*='/careers/']",
            "a[href*='/positions/']",
            "a[href*='/openings/']",
            "a[href*='job_id=']",
            "a[href*='jobId=']",
        ]

        seen_urls: set[str] = set()
        for pattern in job_link_patterns:
            links = page.query_selector_all(pattern)
            for link in links:
                href = link.get_attribute("href") or ""
                title = link.inner_text().strip()

                if not title or len(title) < 5 or len(title) > 200:
                    continue
                if href in seen_urls:
                    continue

                if not href.startswith("http"):
                    from urllib.parse import urljoin
                    href = urljoin(careers_url, href)

                seen_urls.add(href)
                jobs.append(ScannedJob(
                    url=href,
                    title=title,
                    company=company_name,
                    source="careers_page",
                ))
    except Exception as e:
        console.print(f"[yellow]Careers page scan failed for {company_name}: {e}[/yellow]")

    return jobs


def _apply_title_filter(jobs: list[ScannedJob], title_filter: list[str]) -> list[ScannedJob]:
    """Filter jobs by title keywords."""
    if not title_filter:
        return jobs
    patterns = [re.compile(f, re.IGNORECASE) for f in title_filter]
    return [j for j in jobs if any(p.search(j.title) for p in patterns)]


def scan_portals(
    paths: ProjectPaths,
    companies: list[str] | None = None,
    title_filter: list[str] | None = None,
) -> list[ScannedJob]:
    """
    Scan configured job portals for new listings.

    Reads portals.yml for company configurations.
    Returns list of new (not previously seen) jobs.
    """
    from playwright.sync_api import sync_playwright

    portals_config = load_portals(paths)
    if not portals_config:
        console.print(
            "[yellow]No portals.yml found. "
            "Copy templates/portals.example.yml to portals.yml[/yellow]"
        )
        return []

    tracked = portals_config.get("tracked_companies", [])
    if not tracked:
        console.print("[yellow]No tracked_companies in portals.yml[/yellow]")
        return []

    if companies:
        company_set = {c.lower() for c in companies}
        tracked = [t for t in tracked if t.get("name", "").lower() in company_set]

    history = _load_scan_history(paths)
    portal_title_filter = title_filter or portals_config.get("title_filter", [])

    all_jobs: list[ScannedJob] = []
    console.print(f"[bold]Scanning {len(tracked)} companies...[/bold]\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for company_config in tracked:
            name = company_config.get("name", "Unknown")
            careers_url = company_config.get("careers_url", "")
            ats = company_config.get("ats", "").lower()
            slug = company_config.get("slug", "")

            console.print(f"[dim]Scanning {name}...[/dim]")

            jobs: list[ScannedJob] = []

            if ats == "greenhouse" and slug:
                jobs = _scan_greenhouse(page, slug, name)
            elif ats == "lever" and slug:
                jobs = _scan_lever(page, slug, name)
            elif ats == "ashby" and slug:
                jobs = _scan_ashby(page, slug, name)
            elif careers_url:
                jobs = _scan_careers_page(page, careers_url, name)
            else:
                console.print(f"  [yellow]No scannable URL for {name}[/yellow]")
                continue

            if portal_title_filter:
                jobs = _apply_title_filter(jobs, portal_title_filter)

            new_jobs = [j for j in jobs if j.url not in history]
            for j in new_jobs:
                j.is_new = True

            all_jobs.extend(new_jobs)
            console.print(f"  Found {len(jobs)} listings, {len(new_jobs)} new")

        browser.close()

    new_urls = [j.url for j in all_jobs if j.is_new]
    if new_urls:
        _save_scan_history(paths, new_urls)

    # Display results
    if all_jobs:
        console.print(f"\n[bold green]Found {len(all_jobs)} new jobs:[/bold green]\n")
        table = Table(show_header=True, header_style="bold")
        table.add_column("#", justify="right", style="cyan")
        table.add_column("Company")
        table.add_column("Title")
        table.add_column("Location")
        table.add_column("Source")

        for i, job in enumerate(all_jobs, 1):
            table.add_row(
                str(i),
                job.company,
                job.title[:60],
                job.location[:30],
                job.source,
            )
        console.print(table)
    else:
        console.print("[dim]No new jobs found.[/dim]")

    return all_jobs
