"""Job URL liveness checker using Playwright — zero AI tokens."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

console = Console()

EXPIRED_PATTERNS = [
    re.compile(r"job (is )?no longer available", re.I),
    re.compile(r"job.*no longer open", re.I),
    re.compile(r"position has been filled", re.I),
    re.compile(r"this job has expired", re.I),
    re.compile(r"job posting has expired", re.I),
    re.compile(r"no longer accepting applications", re.I),
    re.compile(r"this (position|role|job) (is )?no longer", re.I),
    re.compile(r"this job (listing )?is closed", re.I),
    re.compile(r"job (listing )?not found", re.I),
    re.compile(r"the page you are looking for doesn.t exist", re.I),
    re.compile(r"\d+\s+jobs?\s+found", re.I),
    re.compile(r"search for jobs page is loaded", re.I),
    re.compile(r"diese stelle (ist )?(nicht mehr|bereits) besetzt", re.I),
    re.compile(r"offre (expirée|n'est plus disponible)", re.I),
]

EXPIRED_URL_PATTERNS = [
    re.compile(r"[?&]error=true", re.I),
]

APPLY_PATTERNS = [
    re.compile(r"\bapply\b", re.I),
    re.compile(r"\bsolicitar\b", re.I),
    re.compile(r"\bbewerben\b", re.I),
    re.compile(r"\bpostuler\b", re.I),
    re.compile(r"submit application", re.I),
    re.compile(r"easy apply", re.I),
    re.compile(r"start application", re.I),
    re.compile(r"ich bewerbe mich", re.I),
]

MIN_CONTENT_CHARS = 300


@dataclass
class LivenessResult:
    url: str
    status: str  # "active", "expired", "uncertain"
    reason: str


def check_single_url(page, url: str) -> LivenessResult:
    """Check a single URL for liveness using an existing Playwright page."""
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
        http_status = response.status if response else 0

        if http_status in (404, 410):
            return LivenessResult(url, "expired", f"HTTP {http_status}")

        page.wait_for_timeout(2000)

        final_url = page.url
        for pattern in EXPIRED_URL_PATTERNS:
            if pattern.search(final_url):
                return LivenessResult(url, "expired", f"redirect to {final_url}")

        body_text = page.evaluate("() => document.body?.innerText ?? ''")

        if any(p.search(body_text) for p in APPLY_PATTERNS):
            return LivenessResult(url, "active", "apply button detected")

        for pattern in EXPIRED_PATTERNS:
            if pattern.search(body_text):
                return LivenessResult(
                    url, "expired", f"pattern matched: {pattern.pattern}"
                )

        if len(body_text.strip()) < MIN_CONTENT_CHARS:
            return LivenessResult(
                url, "expired", "insufficient content — likely nav/footer only"
            )

        return LivenessResult(
            url, "uncertain", "content present but no apply button found"
        )

    except Exception as exc:
        return LivenessResult(
            url, "expired", f"navigation error: {str(exc).split(chr(10))[0]}"
        )


def check_liveness(
    urls: list[str],
) -> list[LivenessResult]:
    """
    Check whether job posting URLs are still active.

    Uses Playwright Chromium in headless mode — sequential to avoid rate limits.
    Returns list of LivenessResult.
    """
    from playwright.sync_api import sync_playwright

    console.print(f"Checking {len(urls)} URL(s)...\n")

    results: list[LivenessResult] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for url in urls:
            result = check_single_url(page, url)
            icon = {"active": "[green]✓[/green]", "expired": "[red]✗[/red]", "uncertain": "[yellow]![/yellow]"}
            console.print(f"{icon[result.status]} {result.status:<10} {url}")
            if result.status != "active":
                console.print(f"           [dim]{result.reason}[/dim]")
            results.append(result)

        browser.close()

    active = sum(1 for r in results if r.status == "active")
    expired = sum(1 for r in results if r.status == "expired")
    uncertain = sum(1 for r in results if r.status == "uncertain")

    console.print(f"\nResults: {active} active  {expired} expired  {uncertain} uncertain")

    return results


def check_liveness_from_file(file_path: str | Path) -> list[LivenessResult]:
    """Load URLs from a file (one per line) and check liveness."""
    path = Path(file_path)
    text = path.read_text(encoding="utf-8")
    urls = [
        line.strip()
        for line in text.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    return check_liveness(urls)
