"""Drive one application end to end and log the outcome.

Hybrid control (config `apply.mode`, or per run):
  review  — fill every step, then stop at the final Submit and ask you:
            [s]ubmit · [m]anual (you finish in the browser) · [k] skip
  queue   — `applyloop hunt --mode queue` prepares jobs unattended (score, CV, cover
            letter); `applyloop queue approve ...` marks them; `applyloop submit` then
            fills and submits approved jobs automatically, *but only* when every
            answer came from a trusted source (profile / answer bank / EEO decline)
            and no required field is left. Otherwise it pauses for you (interactive)
            or parks the job as `needs_input` with the exact questions to answer.

Never automated: logins/passwords, account creation, CAPTCHAs, ticking terms or
certification checkboxes. Those always come back to you.
"""

from __future__ import annotations

import re
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .. import store as S
from ..answers import AnswerBank
from ..browser import wait_for_human_if_blocked
from .forms import (NEXT_PATTERNS, SUBMIT_PATTERNS, FormFiller, click_button, find_button,
                    needs_login, page_confirms_submission)

console = Console()

MANUAL_ONLY_HOSTS = re.compile(r"myworkdayjobs\.com|workday|taleo\.net|successfactors|oraclecloud|"
                               r"linkedin\.com|brassring|jobvite\.com/.*/login|ycombinator\.com/companies|workatastartup\.com", re.I)
# Company application systems whose public forms can be filled without an account.
AUTO_ATS_HOSTS = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|workable\.com|"
                            r"bamboohr\.com|recruitee\.com|breezy\.hr|applytojob\.com|jobvite\.com|"
                            r"rippling\.com|pinpointhq\.com|teamtailor\.com|personio\.|dover\.com|"
                            r"jobs\.gem\.com|wellfound\.com|freshteam\.com|comeet\.com|paylocity\.com", re.I)
BOT_WALL_HOSTS = re.compile(r"indeed\.com|glassdoor\.|linkedin\.com|ziprecruiter\.com", re.I)
# Job aggregators: their page is a listing, not the employer's application form.
AGGREGATOR_HOSTS = re.compile(r"adzuna\.|diversity\.com|jooble\.|talent\.com|lensa\.|jobrapido|careerjet|"
                              r"simplyhired|monster\.|dice\.com|theladders|jobright|builtin\.com|themuse\.com|"
                              r"snagajob|careerbuilder|getwork|whatjobs|jobleads|learn4good|recruit\.net|neuvoo|"
                              r"jobs\.institutedata|tallo\.|jobgether|remoterocketship|himalayas\.app|wellfound\.com/l/|"
                              r"tealhq\.com", re.I)


def direct_form_url(job, settings=None) -> str:
    """URL of the application form itself for known ATSs (skips the job page + Apply click)."""
    from urllib.parse import parse_qs, urlparse
    urls = [job["apply_url"] or "", job["url"] or ""]
    for u in urls:
        if "embed/job_app" in u:
            return u
        m = re.search(r"(?:boards|job-boards)\.greenhouse\.io/([\w-]+)/jobs/(\d+)", u)
        if m:
            return f"https://boards.greenhouse.io/embed/job_app?for={m.group(1)}&token={m.group(2)}"
        m = re.search(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})", u)
        if m:
            return f"https://jobs.lever.co/{m.group(1)}/{m.group(2)}/apply"
        m = re.search(r"jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})", u)
        if m:
            return f"https://jobs.ashbyhq.com/{m.group(1)}/{m.group(2)}/application"
    # company page backed by Greenhouse (e.g. stripe.com/jobs/...?gh_jid=123): need the board slug
    if (job["source"] or "") == "greenhouse" and settings is not None:
        for u in urls:
            q = parse_qs(urlparse(u).query)
            jid = (q.get("gh_jid") or [""])[0] or (re.search(r"(\d{6,})", u) or [None, ""])[1]
            if not jid:
                continue
            for c in settings.search.get("companies") or []:
                if str(c.get("ats", "")).lower() == "greenhouse" and \
                        c.get("name", "").lower() == (job["company"] or "").lower():
                    return f"https://boards.greenhouse.io/embed/job_app?for={c['slug']}&token={jid}"
    return ""


def automation_verdict(url: str) -> tuple[bool, str]:
    """Can applyloop apply here without you? (True, why) / (False, why)."""
    u = url or ""
    if BOT_WALL_HOSTS.search(u):
        return False, "job board with bot checks — apply on the site yourself"
    if AGGREGATOR_HOSTS.search(u):
        return False, "job listing site (not the employer's form) — follow its Apply link yourself"
    if MANUAL_ONLY_HOSTS.search(u):
        return False, "portal needs an account (Workday/Taleo/…)"
    if AUTO_ATS_HOSTS.search(u):
        return True, "supported application form"
    return True, "company career site (best effort)"


EASY_APPLY_BTNS = [r"^apply now$", r"^easily apply$", r"^easy apply$", r"^apply with indeed$",
                   r"^apply$", r"indeed apply"]
COMPANY_SITE_BTNS = [r"apply on company site", r"apply on employer site", r"^apply on company website$"]


@dataclass
class ApplyOutcome:
    status: str
    detail: str = ""
    method: str = ""
    screenshots: list[str] = field(default_factory=list)


def _ask(prompt: str, choices: str, default: str) -> str:
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return default
    return ans[:1] if ans[:1] in choices else default


def _shot(page, folder: Path, name: str, enabled: bool) -> str:
    if not enabled:
        return ""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%H%M%S}-{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return ""


def _open_apply_flow(ctx, page, job) -> tuple[object, str]:
    """From the job page, get to the application form. Returns (page, method)."""
    src = (job["source"] or "").lower()
    if job["easy_apply"] and src in ("indeed", "glassdoor"):
        fr, pat = find_button(page, EASY_APPLY_BTNS)
        if fr:
            pages_before = len(ctx.pages)
            click_button(fr, pat)
            page.wait_for_timeout(3000)
            if len(ctx.pages) > pages_before:          # opened in a new tab
                page = ctx.pages[-1]
                page.wait_for_load_state("domcontentloaded")
            return page, "easy_apply"
    target = job["apply_url"]
    if not target and src in ("indeed", "glassdoor"):
        fr, pat = find_button(page, COMPANY_SITE_BTNS)
        if fr:
            pages_before = len(ctx.pages)
            click_button(fr, pat)
            page.wait_for_timeout(3000)
            if len(ctx.pages) > pages_before:
                page = ctx.pages[-1]
                page.wait_for_load_state("domcontentloaded")
            return page, "company_site"
    if target:
        page.goto(target, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
    # Many company pages have an "Apply" button before the form
    fr, pat = find_button(page, [r"^apply( now| for this job)?$", r"^i'?m interested$", r"^start application$"])
    if fr and not find_button(page, SUBMIT_PATTERNS)[0]:
        try:
            click_button(fr, pat)
            page.wait_for_timeout(2500)
        except Exception:
            pass
    return page, "company_site"


def _print_review(job, filler: FormFiller) -> None:
    t = Table(title=f"Review — {job['company']} · {job['title']}", show_lines=False)
    t.add_column("Field")
    t.add_column("Answer")
    t.add_column("Source")
    for f in filler.all_fields:
        if f.source == "existing":
            continue
        color = {"profile": "green", "bank": "green", "eeo": "cyan", "file": "green",
                 "ai": "yellow", "cover": "cyan"}.get(f.source, "red")
        t.add_row(f.label[:60], (f.answer or "—")[:60], f"[{color}]{f.source}[/{color}]")
    console.print(t)


def apply_job(ctx, store: S.Store, job, settings, bank: AnswerBank, *, mode: str,
              interactive: bool, resume: Path | None, cover_letter_pdf=None,
              cover_letter_text=None, approved: bool = False, trust_ai: bool = False,
              cover_policy: str = "required") -> ApplyOutcome:
    shots_on = bool(settings.apply.get("screenshots", True))
    VERIFY_WAIT["seconds"] = int(float(settings.apply.get("email_code_wait_minutes", 15)) * 60)
    folder = settings.root / "output" / "applications" / f"{job['id']:04d}"
    shots: list[str] = []

    if not resume or not Path(resume).exists():
        return ApplyOutcome(S.FAILED, "no CV file for this job")

    page = ctx.new_page()
    try:
        form = direct_form_url(job, settings)
        if form:                                    # known ATS: open the application form directly
            page.goto(form, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            method = "ats_form"
            if not wait_for_human_if_blocked(page, interactive):
                return ApplyOutcome(S.FAILED, "blocked by bot check", method)
        else:
            start = job["url"]
            if BOT_WALL_HOSTS.search(start or "") and job["apply_url"] and \
                    not BOT_WALL_HOSTS.search(job["apply_url"]):
                start = job["apply_url"]                # skip the job-board page entirely
            page.goto(start, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            if not wait_for_human_if_blocked(page, interactive):
                return ApplyOutcome(S.FAILED, "blocked by bot check")
            page, method = _open_apply_flow(ctx, page, job)

        if (AGGREGATOR_HOSTS.search(page.url) or BOT_WALL_HOSTS.search(page.url)) and method != "easy_apply":
            # never type your details into a listing site (e.g. its "email me similar jobs" box)
            if interactive:
                return _handoff(page, job, resume, "this is a job listing page, not the application — "
                                "click through to the employer's form")
            shots.append(_shot(page, folder, "listing-page", shots_on))
            return ApplyOutcome(S.MANUAL, f"landed on a job listing site, not an application form: {page.url}",
                                method, shots)
        if MANUAL_ONLY_HOSTS.search(page.url):
            if interactive:
                return _handoff(page, job, resume, f"{page.url.split('/')[2]} needs an account — finish it in the browser")
            return ApplyOutcome(S.MANUAL, f"site needs account/login: {page.url}", method)
        if needs_login(page):
            if not interactive:
                return ApplyOutcome(S.MANUAL, "login required on application page", method)
            console.print("[yellow]This application asks you to sign in. Sign in in the browser "
                          "(applyloop never types passwords), then press Enter.[/yellow]")
            _ask("", "", "")

        filler = FormFiller(bank, Path(resume), job["title"], job["company"],
                            cover_letter=cover_letter_pdf, cover_text=cover_letter_text,
                            allow_ai=True, cover_policy=cover_policy)
        untrusted, last_sig = [], None
        for step in range(20):
            if not wait_for_human_if_blocked(page, interactive):
                return ApplyOutcome(S.FAILED, "blocked by bot check", method, shots)
            report = filler.fill_step(page)
            untrusted += report.untrusted
            for f in report.filled + report.missing_required:
                if f.source in ("existing", "file"):
                    continue
                store.record_question(job["id"], f.label, f.answer, f.source, f.type,
                                      f.options, f.required)

            if report.missing_required:
                labels = "; ".join(f.label or f.type for f in report.missing_required)[:400]
                if not interactive:
                    shots.append(_shot(page, folder, "needs-input", shots_on))
                    return ApplyOutcome(S.NEEDS_INPUT, f"unanswered required: {labels}", method, shots)
                console.print(Panel(f"Please fill these in the browser, then press Enter:\n{labels}",
                                    title="Needs your input", style="yellow"))
                _ask("", "", "")

            sub_fr, sub_pat = find_button(page, SUBMIT_PATTERNS)
            if sub_fr:
                shots.append(_shot(page, folder, "before-submit", shots_on))
                return _final_step(page, store, job, filler, mode, interactive,
                                   [] if trust_ai else untrusted, approved,
                                   sub_fr, sub_pat, folder, shots, shots_on, method)

            nxt_fr, nxt_pat = find_button(page, NEXT_PATTERNS)
            sig = (page.url, tuple(report.labels))
            if not nxt_fr or sig == last_sig:
                if interactive:
                    return _handoff(page, job, resume, "couldn't find the next/submit button")
                shots.append(_shot(page, folder, "stuck", shots_on))
                return ApplyOutcome(S.NEEDS_INPUT, f"stuck on step {step + 1} ({page.url})", method, shots)
            last_sig = sig
            click_button(nxt_fr, nxt_pat)
            page.wait_for_timeout(2500)
        return ApplyOutcome(S.NEEDS_INPUT, "too many steps", method, shots)
    except Exception as e:
        return ApplyOutcome(S.FAILED, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}", "", shots)
    finally:
        try:
            if not page.is_closed():
                page.close()
        except Exception:
            pass


def _final_step(page, store, job, filler, mode, interactive, untrusted, approved, sub_fr, sub_pat,
                folder, shots, shots_on, method) -> ApplyOutcome:
    auto_ok = (mode == "queue" and approved and not untrusted)
    if not auto_ok:
        if not interactive:
            why = "AI-drafted answers need your review" if untrusted else "review mode"
            return ApplyOutcome(S.NEEDS_INPUT, why, method, shots)
        _print_review(job, filler)
        if untrusted:
            console.print("[yellow]Yellow rows were drafted by AI — check them in the browser.[/yellow]")
        choice = _ask("[s]ubmit · [m]anual (finish in browser) · [k] skip > ", "smk", "m")
        if choice == "k":
            return ApplyOutcome(S.SKIPPED, "skipped at review", method, shots)
        if choice == "m":
            return _handoff(page, job, None, "finish and submit in the browser")
    try:
        click_button(sub_fr, sub_pat)
    except Exception as e:
        return ApplyOutcome(S.FAILED, f"submit click failed: {e}", method, shots)
    for _ in range(10):
        page.wait_for_timeout(1500)
        if page_confirms_submission(page):
            shots.append(_shot(page, folder, "confirmation", shots_on))
            return ApplyOutcome(S.APPLIED, "confirmed", method, shots)
        if asks_email_code(page):
            break
    if asks_email_code(page):
        # "enter the code we emailed you to confirm you're a human" — that's a human check,
        # so YOU type it; applyloop keeps the window open and waits for the confirmation.
        out = _wait_for_email_code(page, job, int(VERIFY_WAIT['seconds']), interactive)
        shots.append(_shot(page, folder, "confirmation" if out else "code-not-entered", shots_on))
        if out:
            return ApplyOutcome(S.APPLIED, "confirmed (you entered the email code)", method, shots)
        return ApplyOutcome(S.NEEDS_INPUT, "email verification code not entered in time — "
                            "rerun `applyloop submit <id>` when you're at the computer", method, shots)
    shots.append(_shot(page, folder, "after-submit", shots_on))
    if interactive and _ask("No confirmation page detected. Did it submit? [y/n] > ", "yn", "n") == "y":
        return ApplyOutcome(S.APPLIED, "confirmed by you", method, shots)
    return ApplyOutcome(S.NEEDS_INPUT, "submit clicked but no confirmation seen — check", method, shots)


CODE_RE = re.compile(r"verification code (was|has been) sent|enter the \d+-character code|security code|"
                     r"confirm you('|’)re a human|code (was|has been) sent to", re.I)
VERIFY_WAIT = {"seconds": 900}


def asks_email_code(page) -> bool:
    try:
        return bool(CODE_RE.search(page.inner_text("body")[-4000:]))
    except Exception:
        return False


def _notify(msg: str) -> None:
    import subprocess
    import sys
    if sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "applyloop" sound name "Glass"'],
                           timeout=10, check=False)
        except Exception:
            pass


def _wait_for_email_code(page, job, wait_s: int, interactive: bool) -> bool:
    msg = f"{job['company']}: enter the code from your email in the browser, then click Submit"
    console.print(Panel(f"{msg}.\nWaiting up to {wait_s // 60} min for the confirmation page…",
                        title="Email code needed", style="yellow"))
    _notify(msg)
    try:
        page.bring_to_front()
    except Exception:
        pass
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            page.wait_for_timeout(3000)
            if page_confirms_submission(page):
                return True
        except Exception:
            return False
    if interactive:
        return _ask("No confirmation seen. Did it submit? [y/n] > ", "yn", "n") == "y"
    return False


def _handoff(page, job, resume, reason: str) -> ApplyOutcome:
    console.print(Panel(f"{reason}\nCV: {resume or 'already attached'}\n"
                        "When you're done, answer below.", title="Over to you", style="cyan"))
    c = _ask("Did you submit it? [y]es · [n]o, skip · [l]ater · [q]uit > ", "ynlq", "l")
    if c == "q":
        return ApplyOutcome("stop", "left for later", "assisted")
    if c == "y":
        return ApplyOutcome(S.APPLIED, "submitted by you", "assisted")
    if c == "n":
        return ApplyOutcome(S.SKIPPED, "you skipped it", "assisted")
    return ApplyOutcome(S.MANUAL, "left for later", "assisted")


def manual_apply(job, resume: Path | None, answers: list[tuple[str, str]]) -> ApplyOutcome:
    """browser.mode=manual — no automation; open the job in your normal browser."""
    from urllib.parse import quote_plus
    from ..sources import clean_company, clean_title
    company, title = clean_company(job["company"] or ""), clean_title(job["title"] or "")
    target = job["apply_url"] or job["url"]
    lines = [f"[bold]{company} — {title}[/bold]", target, f"CV to upload: {resume}"]
    lines += [f"• {q}: {a}" for q, a in answers]
    search = ""
    if BOT_WALL_HOSTS.search(target or "") or AGGREGATOR_HOSTS.search(target or ""):
        # a job board / listing page: also search for the employer's own posting (often on their
        # careers site, ADP, iCIMS, Workday…) so you can apply there directly
        search = "https://www.google.com/search?q=" + quote_plus(f'"{title}" "{company}" careers apply')
        lines.append(f"[dim]Employer's own posting? {search}[/dim]")
        lines.append("[dim]Found a better link? `applyloop queue link <id> <url>` keeps it for next time.[/dim]")
    console.print(Panel("\n".join(lines), title=f"Apply manually · #{job['id']}", style="cyan"))
    try:
        webbrowser.open(target)
        if search:
            webbrowser.open_new_tab(search)
    except Exception:
        pass
    return _handoff(None, job, resume, "Apply in your browser")


def human_delay(settings) -> None:
    lo, hi = (settings.apply.get("delay_seconds") or [45, 120])[:2]
    import random
    secs = random.randint(int(lo), int(hi))
    console.print(f"[dim]Pausing {secs}s before the next application…[/dim]")
    time.sleep(secs)
