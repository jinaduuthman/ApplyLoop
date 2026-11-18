"""Browser sessions for the agent.

Modes (config `browser.mode`, overridable per command with --browser):
  profile  applyloop-owned persistent Chrome profile (~/.applyloop/chrome-profile).
           Run `applyloop login indeed` / `applyloop login glassdoor` once; sessions persist.
           Your everyday browser, passwords and cookies are never touched.
  cdp      attach to a Chrome *you* started with remote debugging, e.g.
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
               --remote-debugging-port=9222 --user-data-dir="$HOME/.applyloop/cdp-profile"
           Only loopback addresses are accepted. applyloop opens its own tabs and never
           closes your browser.
  manual   no automation at all: applyloop prepares CV + answers and opens the job in
           your default browser; you click apply and confirm in the terminal.

applyloop never solves CAPTCHAs or bot checks. If one appears, it pauses and asks you
to complete it in the window (headed modes only).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from rich.console import Console

console = Console()

BLOCK_PATTERNS = re.compile(
    r"verify you are human|additional verification required|just a moment\.\.\.|"
    r"press (and|&) hold|are you a robot|unusual traffic|security check|"
    r"help us protect glassdoor|request blocked", re.I)

LOGIN_URLS = {
    "indeed": "https://secure.indeed.com/auth",
    "glassdoor": "https://www.glassdoor.com/profile/login_input.htm",
    "linkedin": "https://www.linkedin.com/login",
}


class ManualMode(Exception):
    """Raised when the configured browser mode is `manual`."""


def _is_loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


@contextmanager
def open_context(browser_cfg: dict, mode: str | None = None,
                 headless: bool | None = None) -> Iterator:
    """Yield a Playwright BrowserContext for the configured mode."""
    from playwright.sync_api import sync_playwright

    mode = (mode or browser_cfg.get("mode", "profile")).lower()
    if mode == "manual":
        raise ManualMode()
    headless = browser_cfg.get("headless", False) if headless is None else headless

    with sync_playwright() as p:
        if mode == "cdp":
            url = browser_cfg.get("cdp_url", "http://127.0.0.1:9222")
            if not _is_loopback(url):
                raise RuntimeError(f"Refusing non-loopback CDP endpoint {url}")
            browser = p.chromium.connect_over_cdp(url)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            yield ctx       # callers close the tabs they open; your browser stays up
            return

        engine = str(browser_cfg.get("engine", "chromium")).lower()
        default_dir = "~/.applyloop/firefox-profile" if engine == "firefox" else "~/.applyloop/chrome-profile"
        profile_dir = Path(browser_cfg.get("profile_dir") or default_dir).expanduser()
        if engine == "firefox" and profile_dir.name == "chrome-profile":
            profile_dir = profile_dir.with_name("firefox-profile")     # profiles aren't shareable
        if ".applyloop" in profile_dir.parts:                          # keep logins made before the rename
            from .config import prefer_existing
            profile_dir = prefer_existing(profile_dir, Path(str(profile_dir).replace("/.applyloop/", "/.jobpilot/")))
        profile_dir.mkdir(parents=True, exist_ok=True)

        if engine == "firefox":
            for stale in ("parent.lock", "lock", ".parentlock"):      # left behind by a crash/force-quit
                f = profile_dir / stale
                if f.exists() or f.is_symlink():
                    try:
                        f.unlink()
                    except OSError:
                        pass
            try:
                ctx = p.firefox.launch_persistent_context(user_data_dir=str(profile_dir),
                                                          headless=headless, no_viewport=True)
            except Exception as e:
                if "exitCode=0" in str(e):
                    console.print("[red]Firefox exited right away — another applyloop/Firefox window is "
                                  "probably still using this profile. Close every 'Nightly' / applyloop "
                                  "Firefox window (or run: pkill -f firefox-profile) and try again.[/red]")
                raise
        else:
            kwargs = dict(user_data_dir=str(profile_dir), headless=headless, no_viewport=True,
                          args=["--disable-blink-features=AutomationControlled"])
            channel = browser_cfg.get("channel")
            try:
                ctx = p.chromium.launch_persistent_context(channel=channel or None, **kwargs)
            except Exception as e:
                text = str(e)
                if "disallowed by the system admin" in text:
                    console.print(
                        "[red]Your Mac's device management blocks automating Google Chrome "
                        "(\"DevTools remote debugging is disallowed by the system admin\").[/red]\n"
                        "applyloop won't try to override that. Options: set browser.engine: firefox, "
                        "use browser.mode: manual, or run applyloop on a personal machine.")
                    raise RuntimeError("Chrome automation blocked by device policy") from None
                if not channel:
                    raise
                lines = [l for l in text.splitlines() if l.strip()]
                detail = "\n  ".join(lines[:3])
                console.print(f"[yellow]Couldn't start Chrome (channel '{channel}'):\n  {detail}\n"
                              f"Falling back to bundled Chromium…[/yellow]")
                ctx = p.chromium.launch_persistent_context(**kwargs)
        try:
            yield ctx
        finally:
            ctx.close()


def page_text(page) -> str:
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""


def is_blocked(page) -> bool:
    try:
        title = page.title()
    except Exception:
        title = ""
    return bool(BLOCK_PATTERNS.search(title) or BLOCK_PATTERNS.search(page_text(page)[:3000]))


def wait_for_human_if_blocked(page, interactive: bool = True, headless: bool = False) -> bool:
    """Return True if the page is usable. Pauses for the user on bot checks."""
    if not is_blocked(page):
        return True
    if not interactive or headless:
        console.print("[yellow]Bot check detected — skipping (run headed/interactive to resolve it yourself)[/yellow]")
        return False
    console.print("[bold yellow]A verification check appeared in the browser. "
                  "Please complete it there, then press Enter here.[/bold yellow]")
    try:
        input()
    except EOFError:
        return False
    page.wait_for_timeout(1500)
    return not is_blocked(page)


def login(browser_cfg: dict, site: str) -> None:
    url = LOGIN_URLS.get(site, site)
    with open_context(browser_cfg, headless=False) as ctx:
        page = ctx.new_page()
        page.goto(url)
        console.print(f"[bold]Log in to {site} in the opened window "
                      f"(applyloop never sees your password). Press Enter here when done.[/bold]")
        try:
            input()
        except EOFError:
            pass
    console.print(f"[green]Session saved for {site}[/green]")
