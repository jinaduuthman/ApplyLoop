"""CLI commands for the job-search agent (registered onto the main `applyloop` app)."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()

queue_app = typer.Typer(help="Review / approve prepared applications (queue mode)")
answers_app = typer.Typer(help="Screening-question answer bank")
sheets_app = typer.Typer(help="Google Sheets mirror of the job log")
llm_app = typer.Typer(help="AI provider: list models, test a call, see spend")


AI_HELP = "AI provider for this run: openai | anthropic | gemini | ollama (default: llm.use)"


def _agent(interactive: bool = True, provider: str | None = None, search_overrides: dict | None = None):
    from .agent import Agent
    ag = Agent(interactive=interactive, provider=provider)
    if search_overrides:
        ag.settings.search.update({k: v for k, v in search_overrides.items() if v is not None})
    return ag


def _store():
    from .config import ProjectPaths
    from .settings import load_settings
    from .store import Store
    return Store(load_settings(ProjectPaths()).db_path)


def apply_route(r) -> tuple[str, str]:
    """(where the application lives, who applies: auto | you | ?)."""
    from urllib.parse import urlparse
    from .apply_engine.engine import automation_verdict, direct_form_url
    target = r["apply_url"] or r["url"] or ""
    host = urlparse(target).netloc.replace("www.", "")
    if "adzuna." in host:
        return "adzuna (not resolved)", "?"
    ok, _ = automation_verdict(target)
    return host, "auto" if ok or direct_form_url(r) else "you"


def _jobs_table(rows, title: str) -> Table:
    t = Table(title=title)
    for c, j in (("#", "right"), ("Score", "right"), ("Track", "left"), ("Company", "left"),
                 ("Title", "left"), ("Apply at", "left"), ("Who", "center"), ("CV", "center"),
                 ("Status", "left")):
        t.add_column(c, justify=j)
    for r in rows:
        host, who = apply_route(r)
        t.add_row(str(r["id"]), f"{r['score']:.1f}" if r["score"] is not None else "—",
                  (r["track"] or "—")[:18], (r["company"] or "")[:24], (r["title"] or "")[:40],
                  host[:28], {"auto": "[green]auto[/green]", "you": "[yellow]you[/yellow]"}.get(who, "?"),
                  "tailored" if r["cv_tailored"] else ("base" if r["cv_pdf"] else ""),
                  f"{r['status']}" + (f" · {r['status_detail'][:40]}" if r["status_detail"] and r["status"] in ("needs_input", "failed", "manual") else ""))
    return t


def register(app: typer.Typer) -> None:
    app.add_typer(queue_app, name="queue")
    app.add_typer(answers_app, name="answers")
    app.add_typer(sheets_app, name="sheets")
    app.add_typer(llm_app, name="llm")
    app.add_typer(companies_app, name="companies")
    app.add_typer(schedule_app, name="schedule")
    app.add_typer(alerts_app, name="alerts")
    app.add_typer(teal_app, name="teal")
    app.command("init")(init)

    @app.command()
    def login(site: str = typer.Argument(..., help="indeed | glassdoor | linkedin | any URL"),
              browser: Optional[str] = typer.Option(None, "--browser", help="profile | cdp")):
        """Open the agent's browser so you can sign in once (session is reused)."""
        from .browser import login as do_login
        from .config import ProjectPaths
        from .settings import load_settings
        cfg = dict(load_settings(ProjectPaths()).browser)
        if browser:
            cfg["mode"] = browser
        do_login(cfg, site)

    @app.command()
    def hunt(
        mode: Optional[str] = typer.Option(None, "--mode", help="review | queue | auto (default: config apply.mode)"),
        source: Optional[list[str]] = typer.Option(None, "--source", "-s", help="adzuna, remotive, alerts, indeed, glassdoor"),
        track: Optional[list[str]] = typer.Option(None, "--track", "-t", help="limit to track id(s)"),
        max_queries: Optional[int] = typer.Option(None, "--max-queries", help="queries per track"),
        max_apply: Optional[int] = typer.Option(None, "--max-apply", help="review mode: max applications this run"),
        browser: Optional[str] = typer.Option(None, "--browser", help="profile | cdp | manual"),
        unattended: bool = typer.Option(False, "--unattended", help="never prompt (for schedules)"),
        days: Optional[int] = typer.Option(None, "--days", "-d", help="only jobs posted within N days"),
        remote: bool = typer.Option(False, "--remote", "--remote-only",
                                    help="only remote jobs this run (default: your allowed_locations + remote)"),
        work_mode: Optional[list[str]] = typer.Option(None, "--work-mode", "-w", help="remote | hybrid | onsite (repeatable)"),
        ai: Optional[str] = typer.Option(None, "--ai", help=AI_HELP),
    ):
        """Find jobs → filter → score → pick/tailor CV → (review mode) apply with you."""
        if unattended:
            from .config import ProjectPaths
            from .settings import load_settings
            mode = mode or load_settings(ProjectPaths()).apply_mode
            if mode not in ("queue", "auto"):
                mode = "queue"
        overrides = {"posted_within_days": days, "remote_run": remote or None,
                     "work_mode": list(work_mode) if work_mode else None}
        _agent(not unattended, ai, overrides).hunt(mode, source, track, max_queries, max_apply, browser)

    @app.command()
    def submit(
        ids: Optional[list[int]] = typer.Argument(None, help="job ids (default: all approved)"),
        manual: bool = typer.Option(False, "--manual", help="no automation: open in your browser, you apply"),
        review: bool = typer.Option(False, "--review", help="pause at the final submit for each job"),
        browser: Optional[str] = typer.Option(None, "--browser", help="profile | cdp | manual"),
        unattended: bool = typer.Option(False, "--unattended", help="never prompt; park anything uncertain"),
        track: Optional[list[str]] = typer.Option(None, "--track", "-t", help="only these track(s)"),
        limit: Optional[int] = typer.Option(None, "--limit", help="at most N jobs"),
        remote: bool = typer.Option(False, "--remote", "--remote-only",
                                    help="only remote jobs (default: your allowed_locations + remote)"),
        max_age: Optional[int] = typer.Option(None, "--max-age",
                                              help="only jobs posted within N days, e.g. --max-age 3 (default: all; older ones stay on your list)"),
        ai: Optional[str] = typer.Option(None, "--ai", help=AI_HELP),
    ):
        """Apply to approved jobs (or the given ids).

        `applyloop submit --manual` with no ids walks your apply-yourself list one job after another."""
        _agent(not unattended, ai, {"remote_run": remote or None}).submit(
            ids, "review" if review else "queue", manual, browser, track, limit, max_age)

    @app.command()
    def add(
        url: str = typer.Argument(..., help="job posting URL (any site)"),
        jd_file: Optional[str] = typer.Option(None, "--jd-file", "-f", help="text file with the job description"),
        paste: bool = typer.Option(False, "--paste", "-p", help="paste the description in the terminal"),
        title: str = typer.Option("", "--title", "-t"),
        company: str = typer.Option("", "--company", "-c"),
        location: str = typer.Option("", "--location", "-l"),
        salary: str = typer.Option("", "--salary"),
        browser: bool = typer.Option(False, "--browser", help="fetch Indeed/Glassdoor pages with the agent browser"),
        ai: Optional[str] = typer.Option(None, "--ai", help=AI_HELP),
    ):
        """Add a job you found yourself → filter, score, pick/tailor CV, queue it.

        Then: `applyloop submit <id> --manual` (you apply) or `applyloop submit <id>`."""
        import sys
        desc = ""
        if jd_file:
            desc = Path(jd_file).read_text(encoding="utf-8")
        elif paste:
            console.print("Paste the job description, then press Ctrl-D on an empty line:")
            desc = sys.stdin.read()
        ag = _agent(True, ai)
        if not desc and browser and ("indeed." in url or "glassdoor." in url):
            from .browser import open_context
            from .sources import JobPosting
            from .agent import BROWSER_SEARCH
            src = "indeed" if "indeed." in url else "glassdoor"
            with open_context(ag.settings.browser) as ctx:
                p = BROWSER_SEARCH[src].fetch_details(ctx, JobPosting(url=url, title=title, company=company,
                                                                      source=src), True)
                desc = p.description
        if not desc and ("indeed." in url or "glassdoor." in url):
            console.print("[yellow]Indeed/Glassdoor pages can't be read without a browser. "
                          "Paste the description (Ctrl-D to finish), or rerun with --browser:[/yellow]")
            desc = sys.stdin.read()
        if not title and not desc:
            title = typer.prompt("Job title")
        if not company and ("indeed." in url or "glassdoor." in url):
            company = typer.prompt("Company")
        ag.add_job(url, desc.strip(), title, company, location, salary)

    @app.command("log")
    def log_cmd(
        all_: bool = typer.Option(False, "--all", help="every job, not just submitted ones"),
        since: Optional[str] = typer.Option(None, "--since", help="YYYY-MM-DD"),
        export: Optional[str] = typer.Option(None, "--export", help="path ending .csv or .xlsx"),
    ):
        """Show (or export) the log of applications."""
        from .store import SUBMITTED_STATUSES
        st = _store()
        rows = [r for r in st.all_jobs() if all_ or r["status"] in SUBMITTED_STATUSES]
        if since:
            rows = [r for r in rows if (r["applied_at"] or r["found_at"] or "") >= since]
        if export:
            from .sheets import COLUMNS, row_values
            out = Path(export)
            if out.suffix == ".xlsx":
                from openpyxl import Workbook
                wb = Workbook()
                ws = wb.active
                ws.append(COLUMNS)
                for r in rows:
                    ws.append(row_values(r))
                wb.save(out)
            else:
                with open(out, "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(COLUMNS)
                    w.writerows(row_values(r) for r in rows)
            console.print(f"[green]Exported {len(rows)} rows → {out}[/green]")
            return
        console.print(_jobs_table(rows, f"Applications ({len(rows)})"))

    @app.command()
    def refilter():
        """Re-apply your current filters (titles, salary, age, remote...) to jobs not yet applied to."""
        from .filters import filter_reason
        from .store import FILTERED
        ag = _agent(False)
        n = 0
        from .sources import clean_company, clean_title
        for r in ag.store.by_status("found", "scored", "queued", "approved", "needs_input", "low_score", "manual"):
            c, t = clean_company(r["company"] or ""), clean_title(r["title"] or "")
            if (c, t) != (r["company"], r["title"]):                # "Acme\n4.1" → "Acme"
                ag.store.update_job(r["id"], company=c, title=t)
                r = ag.store.get(r["id"])
            # titles checked against every track, paused ones too, so their waiting jobs survive
            why = filter_reason(dict(r), ag.settings.filters, ag.settings.all_tracks or ag.settings.tracks,
                                ag.settings.search)
            if why:
                ag.store.set_status(r["id"], FILTERED, why)
                n += 1
        # collapse reposts of the same opening (other cities / sites): keep the best-scored one
        from .urls import role_key
        best: dict = {}
        for r in ag.store.by_status("queued", "approved", "needs_input", "manual", "applied"):
            k = role_key(r["company"], r["title"])
            keep = best.get(k)
            if keep is None or (r["status"] == "applied" and keep["status"] != "applied") or \
                    (keep["status"] != "applied" and (r["score"] or 0) > (keep["score"] or 0)):
                if keep is not None and keep["status"] != "applied":
                    ag.store.set_status(keep["id"], "skipped", f"duplicate of #{r['id']}")
                    n += 1
                best[k] = r
            elif r["status"] != "applied":
                ag.store.set_status(r["id"], "skipped", f"duplicate of #{keep['id']}")
                n += 1
        for r in ag.store.by_status("queued", "approved", "manual"):      # same role under another company name
            dup = ag.already_applied(r)
            if dup:
                ag.store.set_status(r["id"], "skipped", f"already applied as #{dup}")
                n += 1
        console.print(f"[green]{n} job(s) removed by the current filters / as duplicates.[/green] "
                      "`applyloop queue list` to see what's left.")

    @app.command()
    def mark(job_id: int, status: str = typer.Argument(..., help="responded | interview | offer | rejected | applied | skipped"),
             note: str = typer.Option("", "--note", "-n")):
        """Record what happened next (recruiter reply, interview, offer...)."""
        st = _store()
        st.set_status(job_id, status, note)
        console.print(f"[green]#{job_id} → {status}[/green]")

    @app.command()
    def stats():
        """Funnel by status, track and source."""
        st = _store()
        rows = st.all_jobs()
        t = Table(title="Funnel")
        t.add_column("Status")
        t.add_column("Jobs", justify="right")
        for k, v in sorted(st.counts().items(), key=lambda kv: -kv[1]):
            t.add_row(k, str(v))
        console.print(t)
        from .store import SUBMITTED_STATUSES
        by = Table(title="Submitted by track / source")
        by.add_column("Track")
        by.add_column("Source")
        by.add_column("Applied", justify="right")
        by.add_column("Responses", justify="right")
        agg: dict = {}
        for r in rows:
            if r["status"] in SUBMITTED_STATUSES:
                k = (r["track"] or "—", r["source"] or "—")
                a = agg.setdefault(k, [0, 0])
                a[0] += 1
                a[1] += r["status"] in ("responded", "interview", "offer")
        for (tr, src), (n, resp) in sorted(agg.items()):
            by.add_row(tr, src, str(n), str(resp))
        console.print(by)
        console.print(f"Submitted today: {st.submitted_on(date.today())}")

    @app.command("cv")
    def cv_cmd(
        job_id: Optional[int] = typer.Argument(None, help="show the CV chosen for a job"),
        track: Optional[str] = typer.Option(None, "--track", help="preview tailoring for a track"),
        jd_file: Optional[str] = typer.Option(None, "--jd-file", help="JD text file for preview"),
        no_ai: bool = typer.Option(False, "--no-ai", help="deterministic edits only"),
        ai: Optional[str] = typer.Option(None, "--ai", help=AI_HELP),
    ):
        """Inspect the CV for a job, or preview tailoring a track's .tex against a JD."""
        if job_id is not None:
            r = _store().get(job_id)
            console.print(f"[bold]{r['company']} — {r['title']}[/bold]\nPDF: {r['cv_pdf']}\nTeX: {r['cv_tex']}\n")
            console.print(r["tailor_notes"] or "(no notes)")
            return
        from .agent import Agent
        from .llm import LLM, LLMConfig
        ag = Agent(provider=ai)
        tr = ag.settings.track(track) if track else ag.settings.tracks[0]
        jd = Path(jd_file).read_text() if jd_file else typer.prompt("Paste JD text")
        from .tailor_tex import gap_report, prepare_cv
        res = prepare_cv(ag.settings, tr, 0, "preview", "preview", jd,
                         LLM(LLMConfig()) if no_ai else ag.ai("tailor"), mode="always")
        console.print(f"PDF: {res.pdf} ({res.pages} page(s), tailored={res.tailored})")
        console.print("\n".join(res.notes))
        if res.gap:
            console.print(gap_report(res.gap))

    # ── queue ────────────────────────────────────────────────────────
    @queue_app.command("list")
    def queue_list(status: list[str] = typer.Option(["queued", "approved", "needs_input"], "--status", "-s"),
                   who: Optional[str] = typer.Option(None, "--who", help="auto | you | ? (unresolved)")):
        """Jobs waiting for you. 'Who': auto = applyloop can apply on its own · you = needs you."""
        rows = _store().by_status(*status)
        if who:
            rows = [r for r in rows if apply_route(r)[1] == who]
        n_auto = sum(1 for r in rows if apply_route(r)[1] == "auto")
        console.print(_jobs_table(rows, f"Queue ({', '.join(status)}) — {len(rows)} jobs · {n_auto} auto"))

    @app.command()
    def resolve(ids: Optional[list[int]] = typer.Argument(None, help="job ids (default: every queued Adzuna job)"),
                limit: Optional[int] = typer.Option(None, "--limit", help="at most N links"),
                retry: bool = typer.Option(False, "--retry", help="also retry links that failed before")):
        """Follow Adzuna links once to see where each job really applies (auto vs you)."""
        from .browser import ManualMode, open_context
        ag = _agent(False)
        try:
            with open_context(ag.settings.browser) as ctx:
                ag.resolve_links(ctx, ids, limit, skip_failed=not retry and not ids)
        except ManualMode:
            console.print("[yellow]browser.mode=manual — resolving needs the agent browser.[/yellow]")
            return
        console.print("See them: `applyloop queue list --who auto`")

    @queue_app.command("show")
    def queue_show(job_id: int):
        """Everything about one job: evaluation, CV changes, questions, history."""
        st = _store()
        r = st.get(job_id)
        if not r:
            raise typer.Exit(1)
        console.rule(f"#{r['id']} {r['company']} — {r['title']}")
        console.print(f"{r['location']} · {r['salary'] or 'salary n/a'} · {r['source']} · "
                      f"{'Easy Apply' if r['easy_apply'] else 'company site'}\n{r['url']}")
        console.print(f"Score {r['score']} ({r['grade']}) · track {r['track']} · status {r['status']} {r['status_detail'] or ''}")
        if r["eval_json"]:
            ev = json.loads(r["eval_json"])
            console.print(f"\n[italic]{ev.get('summary', '')}[/italic]")
            for g in ev.get("gaps", [])[:4]:
                console.print(f"  [red]-[/red] {g}")
            if ev.get("report"):
                console.print(f"[dim]report: {ev['report']}[/dim]")
        console.print(f"\n[bold]CV[/bold]: {r['cv_pdf']}\n[dim]{r['tailor_notes'] or ''}[/dim]")
        qs = st.questions_for(job_id)
        if qs:
            t = Table(title="Questions")
            t.add_column("Question")
            t.add_column("Answer")
            t.add_column("Source")
            for q in qs:
                t.add_row(q["question"][:60], (q["answer"] or "—")[:50], q["source"])
            console.print(t)
        for e in st.events(job_id):
            console.print(f"[dim]{e['at']}  {e['status']:<12} {e['detail'] or ''}[/dim]")

    @queue_app.command("approve")
    def queue_approve(ids: Optional[list[int]] = typer.Argument(None),
                      all_: bool = typer.Option(False, "--all", help="approve every queued job"),
                      min_score: float = typer.Option(0.0, "--min-score")):
        """Approve jobs for `applyloop submit`."""
        st = _store()
        rows = st.by_status("queued", "needs_input") if all_ else [st.get(i) for i in ids or []]
        n = 0
        for r in rows:
            if r and (r["score"] or 0) >= min_score:
                st.set_status(r["id"], "approved", "approved by you")
                n += 1
        console.print(f"[green]Approved {n} job(s). Run `applyloop submit`.[/green]")

    @queue_app.command("link")
    def queue_link(job_id: int, url: str = typer.Argument(..., help="the employer's own posting / application link")):
        """Use this link to apply (e.g. the employer's careers page instead of Glassdoor/LinkedIn)."""
        from .apply_engine.engine import automation_verdict
        st = _store()
        r = st.get(job_id)
        if not r:
            raise typer.Exit(1)
        st.update_job(job_id, apply_url=url, notes=f"link set by you: {url}")
        ok, why = automation_verdict(url)
        if ok and r["status"] == "manual":
            st.set_status(job_id, "queued", "employer form link added by you")
        console.print(f"[green]#{job_id} → {url}[/green]  "
                      + ("applyloop can fill this one: `applyloop queue approve " + str(job_id) + "` then `applyloop submit "
                         + str(job_id) + "`" if ok else f"[yellow]{why}[/yellow] — `applyloop submit {job_id} --manual`"))

    @queue_app.command("reject")
    def queue_reject(ids: list[int], note: str = typer.Option("", "--note", "-n")):
        """Skip jobs you don't want."""
        st = _store()
        for i in ids:
            st.set_status(i, "skipped", note or "rejected by you")
        console.print(f"Skipped {len(ids)} job(s)")

    # ── answers ──────────────────────────────────────────────────────
    @answers_app.command("pending")
    def answers_pending():
        """Questions applyloop couldn't answer from your bank — add rules for them."""
        rows = _store().unanswered_questions()
        t = Table(title="Add these to config/answers.yml")
        t.add_column("Seen", justify="right")
        t.add_column("Question")
        t.add_column("Options")
        for r in rows:
            t.add_row(str(r["n"]), r["question"][:80], ", ".join(json.loads(r["options"] or "[]"))[:50])
        console.print(t)

    @answers_app.command("test")
    def answers_test(question: str, options: Optional[list[str]] = typer.Option(None, "--option", "-o")):
        """See how a question would be answered (no AI)."""
        from .agent import Agent
        a = Agent().bank(None).resolve(question, options=options or [], allow_ai=False)
        console.print(f"{a.value!r}  (source: {a.source})")

    # ── sheets ───────────────────────────────────────────────────────
    @sheets_app.command("sync")
    def sheets_sync():
        """Rewrite the Google Sheet from the local log."""
        from .config import ProjectPaths
        from .settings import load_settings
        from .sheets import SheetSync
        s = load_settings(ProjectPaths())
        n = SheetSync(s.sheets).sync_all(_store().all_jobs())
        console.print(f"[green]Synced {n} rows to Google Sheets[/green]")


def _pool(provider: str | None = None):
    from .config import ProjectPaths
    from .llm import LLMPool
    from .settings import load_settings
    return LLMPool(load_settings(ProjectPaths()).llm, usage_cb=_store().record_usage, provider=provider)


@llm_app.command("show")
def llm_show(ai: Optional[str] = typer.Option(None, "--ai", help=AI_HELP)):
    """Which provider/model each task uses."""
    from .llm import TASKS
    pool = _pool(ai)
    t = Table(title="AI per task")
    t.add_column("Task")
    t.add_column("Provider:model")
    t.add_column("Key set")
    t.add_column("Price $/M in/out")
    for task in ("",) + TASKS:
        c = pool.for_task(task).config
        price = c.prices.get(c.model)
        t.add_row(task or "default", f"{c.provider}:{c.model}" if c.provider else "none",
                  "yes" if (c.api_key or c.provider == "ollama") else "[red]no[/red]",
                  f"{price[0]} / {price[1]}" if price else "[dim]add to llm.prices[/dim]")
    console.print(t)


@llm_app.command("models")
def llm_models(contains: str = typer.Option("", "--grep", help="filter, e.g. gpt or gemini"),
               ai: Optional[str] = typer.Option(None, "--ai", help=AI_HELP)):
    """List model IDs your key can use (copy one into config/applyloop.yml)."""
    llm = _pool(ai).default
    if not llm.config.api_key and llm.config.provider != "ollama":
        console.print("[red]No API key for the configured provider in .env[/red]")
        raise typer.Exit(1)
    ids = [m for m in llm.list_models() if contains.lower() in m.lower()]
    console.print("\n".join(ids) or "(none)")


@llm_app.command("test")
def llm_test(task: str = typer.Option("", "--task", help="score | route | tailor | answers | cover"),
             ai: Optional[str] = typer.Option(None, "--ai", help=AI_HELP)):
    """Send one tiny request and report latency, tokens and cost."""
    import time as _t
    llm = _pool(ai).for_task(task)
    if not llm.available:
        console.print(f"[red]Not configured: {llm.describe()} — set the key in .env and llm.model[/red]")
        raise typer.Exit(1)
    t0 = _t.time()
    data = llm.complete_json('Reply with {"ok": true, "model": "<your model name>"}', max_tokens=200)
    console.print(f"[green]{llm.describe()} OK in {_t.time() - t0:.1f}s → {data}[/green]")
    rows = _store().usage_summary()
    if rows:
        console.print(f"[dim]logged {rows[0]['inp']} in / {rows[0]['out']} out tokens[/dim]")


@llm_app.command("usage")
def llm_usage(since: str = typer.Option("", "--since", help="YYYY-MM-DD (default: all time)")):
    """AI calls, tokens and estimated spend per model and task."""
    rows = _store().usage_summary(since)
    t = Table(title=f"AI usage{' since ' + since if since else ''}")
    for c in ("Model", "Task", "Calls", "Input tok", "Output tok", "Est. $"):
        t.add_column(c, justify="right" if c not in ("Model", "Task") else "left")
    total = 0.0
    for r in rows:
        total += r["cost"] or 0
        t.add_row(r["model"], r["task"], str(r["calls"]), f"{r['inp']:,}", f"{r['out']:,}",
                  f"{r['cost']:.3f}" if r["cost"] is not None else "?")
    console.print(t)
    console.print(f"[bold]Estimated total: ${total:.2f}[/bold]  "
                  "[dim](estimates from llm.prices; your provider dashboard is authoritative)[/dim]")


@llm_app.command("use")
def llm_use(provider: str = typer.Argument(..., help="openai | anthropic | gemini | ollama | auto")):
    """Switch the default AI provider (edits `use:` in config/applyloop.yml)."""
    import re as _re
    from .config import ProjectPaths
    provider = provider.lower()
    if provider not in ("openai", "anthropic", "gemini", "ollama", "auto"):
        raise typer.BadParameter("openai | anthropic | gemini | ollama | auto")
    cfg = ProjectPaths().config_dir / "applyloop.yml"
    text = cfg.read_text(encoding="utf-8")
    new, n = _re.subn(r"(?m)^(  use:\s*)\S+", lambda m: m.group(1) + provider, text, count=1)
    if not n:
        raise typer.BadParameter("No `use:` line under llm: in config/applyloop.yml")
    cfg.write_text(new, encoding="utf-8")
    console.print(f"[green]Default AI provider → {provider}[/green]")
    llm_show(None)


companies_app = typer.Typer(help="Company job boards (Greenhouse / Lever / Ashby public APIs)")


@companies_app.command("check")
def companies_check():
    """Verify every company in search.companies resolves, and count open roles."""
    from .config import ProjectPaths
    from .settings import load_settings
    from .sources import ats_api
    s = load_settings(ProjectPaths())
    rows = s.search.get("companies") or []
    if not rows:
        console.print("No companies configured (search.companies in config/applyloop.yml).")
        return
    t = Table(title="Company boards")
    for c in ("Company", "ATS", "Slug", "Open roles", "Status"):
        t.add_column(c)
    for c in rows:
        try:
            n = len(ats_api.fetch_company(c, 0, ["data engineer"]) if c.get("ats") == "workday"
                    else ats_api.fetch_company(c, 0))
            t.add_row(c.get("name", ""), c.get("ats", ""), c.get("slug", "") or c.get("url", ""), str(n),
                      "[green]ok[/green]")
        except Exception as e:
            t.add_row(c.get("name", ""), c.get("ats", ""), c.get("slug", ""), "—",
                      f"[red]{str(e)[:50]}[/red] — fix slug or remove")
    console.print(t)


def init_project(target: Path, force: bool = False) -> list[tuple[str, str]]:
    """Create an applyloop folder from the packaged templates. Never overwrites unless force."""
    import shutil
    from .config import STARTER_DIR
    target = target.expanduser().resolve()
    plan = [("applyloop.example.yml", "config/applyloop.yml"), ("profile.example.yml", "config/profile.yml"),
            ("answers.example.yml", "config/answers.yml"), ("eeo.example.yml", "config/eeo.yml"),
            ("env.example", ".env"), ("resumes_README.md", "resumes/README.md")]
    done = []
    for src, dst in plan:
        out = target / dst
        if out.exists() and not force:
            done.append((dst, "kept (already exists)"))
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STARTER_DIR / src, out)
        if dst == ".env":
            out.chmod(0o600)
        done.append((dst, "created"))
    for d in ("data", "output"):
        (target / d).mkdir(parents=True, exist_ok=True)
    gi = target / ".gitignore"
    if not gi.exists():
        gi.write_text(".env\nconfig/applyloop.yml\nconfig/profile.yml\nconfig/answers.yml\nconfig/eeo.yml\n"
                      "resumes/*\n!resumes/README.md\ndata/\noutput/\n", encoding="utf-8")
        done.append((".gitignore", "created"))
    return done


def init(
    directory: str = typer.Argument(".", help="folder to set up (default: current folder)"),
    force: bool = typer.Option(False, "--force", help="overwrite existing files with fresh templates"),
):
    """Set up an applyloop folder: config templates, .env, resumes/, data/, output/."""
    target = Path(directory)
    for dst, what in init_project(target, force):
        console.print(f"  {'[green]+[/green]' if what == 'created' else '[dim]=[/dim]'} {dst}  [dim]{what}[/dim]")
    console.print(f"""
[bold]Next:[/bold]
  1. {target / '.env'}  — add an AI key (OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY) and Adzuna keys
  2. config/profile.yml — your name, email, phone, links, location
  3. resumes/           — your CV(s); point config/applyloop.yml → tracks[].cv at them
  4. config/answers.yml — review every screening answer (work authorization, sponsorship, …)
  5. playwright install firefox   ·   applyloop doctor   ·   applyloop hunt --mode review --days 3
Run applyloop from this folder (or set APPLYLOOP_HOME={target.expanduser().resolve()}).""")


@companies_app.command("discover")
def companies_discover(query: Optional[str] = typer.Argument(None, help='title to search, e.g. "Data Engineer" (default: your track titles)'),
                       days: int = typer.Option(7, "--days")):
    """Search Greenhouse / Lever / Ashby / Workable / YC for employers hiring for your titles (nothing saved)."""
    from .config import ProjectPaths
    from .settings import load_settings
    from .sources import ats_search
    s = load_settings(ProjectPaths())
    if not ats_search.provider():
        console.print("[red]Add BRAVE_API_KEY (https://brave.com/search/api) or SERPAPI_KEY to .env first.[/red]")
        raise typer.Exit(1)
    cfg = s.search.get("ats_search") or {}
    queries = [query] if query else list(dict.fromkeys(q for t in s.tracks for q in t.queries))
    known = {(str(c.get("ats", "")).lower(), str(c.get("slug", "")).lower()) for c in s.search.get("companies") or []}
    boards, direct = ats_search.discover(queries, days, cfg.get("sites"), 1 if query else int(cfg.get("max_queries", 6)),
                                         known)
    t = Table(title=f"{len(boards)} new employer boards (not already in your config) · {len(direct)} YC jobs")
    for c in ("Company", "System", "Board"):
        t.add_column(c)
    for b in boards:
        t.add_row(b["name"], b["ats"], b["slug"])
    for j in direct:
        t.add_row(f"{j.company} — {j.title[:40]}", "yc", j.url[:50])
    console.print(t)


schedule_app = typer.Typer(help="Run applyloop automatically every day (macOS launchd)")
PLIST_LABEL = "com.applyloop.hunt"
LEGACY_PLIST_LABEL = "com.jobpilot.hunt"   # from before the rename; `schedule install/remove` clears it


def _plist_path(label: str = PLIST_LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


@schedule_app.command("install")
def schedule_install(time: str = typer.Option("08:05", "--time", help="HH:MM local time"),
                     weekdays_only: bool = typer.Option(False, "--weekdays", help="Mon–Fri only"),
                     mode: str = typer.Option("auto", "--mode", help="auto | queue")):
    """Install a daily unattended run: `applyloop hunt --unattended --mode <mode>`."""
    import plistlib
    import shutil
    import subprocess
    import sys
    from .config import ProjectPaths
    hh, mm = (int(x) for x in time.split(":"))
    exe = shutil.which("applyloop") or shutil.which("applyloop") or str(Path(sys.argv[0]).resolve())
    root = ProjectPaths().root
    log = root / "output" / "logs"
    log.mkdir(parents=True, exist_ok=True)
    days = range(1, 6) if weekdays_only else [None]
    intervals = [{"Hour": hh, "Minute": mm, **({"Weekday": d} if d else {})} for d in days]
    plist = {
        "Label": PLIST_LABEL,
        "ProgramArguments": [exe, "hunt", "--unattended", "--mode", mode],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": intervals if weekdays_only else intervals[0],
        "StandardOutPath": str(log / "hunt.log"),
        "StandardErrorPath": str(log / "hunt.log"),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/Library/TeX/texbin"},
    }
    old = _plist_path(LEGACY_PLIST_LABEL)
    if old.exists():                                   # the pre-rename schedule — replace it
        if sys.platform == "darwin":
            subprocess.run(["launchctl", "unload", str(old)], capture_output=True)
        old.unlink()
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(plist, f)
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        subprocess.run(["launchctl", "load", str(path)], check=False)
    console.print(f"[green]Scheduled daily at {time}{' (Mon–Fri)' if weekdays_only else ''}: "
                  f"applyloop hunt --unattended --mode {mode}[/green]\nLog: {log / 'hunt.log'} · "
                  f"digest: output/digests/ · remove with `applyloop schedule remove`")


@schedule_app.command("remove")
def schedule_remove():
    """Stop the daily run."""
    import subprocess
    import sys
    found = False
    for path in (_plist_path(), _plist_path(LEGACY_PLIST_LABEL)):
        if path.exists():
            if sys.platform == "darwin":
                subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
            path.unlink()
            found = True
    console.print("[green]Daily run removed.[/green]" if found else "No schedule installed.")


@schedule_app.command("status")
def schedule_status():
    """Show the installed schedule and the last log lines."""
    import plistlib
    path = _plist_path()
    if not path.exists():
        console.print("No schedule installed (`applyloop schedule install`).")
        return
    p = plistlib.loads(path.read_bytes())
    console.print(f"{' '.join(p['ProgramArguments'][1:])} · {p['StartCalendarInterval']}")
    log = Path(p.get("StandardOutPath", ""))
    if log.exists():
        console.print("[dim]" + "\n".join(log.read_text(errors="replace").splitlines()[-15:]) + "[/dim]")


teal_app = typer.Typer(help="Teal (tealhq.com) public job board")


@teal_app.command("test")
def teal_test(query: str = typer.Argument("Data Engineer", help="job title to look up"),
              days: int = typer.Option(7, "--days"),
              dump: Optional[str] = typer.Option(None, "--dump", help="save the raw Teal pages (HTML) to this folder")):
    """Fetch Teal postings for a title and show which pass your filters (nothing is saved)."""
    from .config import ProjectPaths
    from .filters import filter_reason
    from .settings import load_settings
    from .sources import teal
    s = load_settings(ProjectPaths())
    try:
        jobs = teal.search(query, days, 60, dump_dir=dump)
    except Exception as e:
        console.print(f"[red]Teal fetch failed: {e}[/red]")
        raise typer.Exit(1)
    t = Table(title=f"Teal “{query}”, last {days} days: {len(jobs)} postings")
    for c in ("Title", "Company", "Location", "Posted", "Kept?"):
        t.add_column(c)
    for j in jobs:
        why = filter_reason({"title": j.title, "company": j.company, "salary": j.salary, "posted": j.posted,
                             "location": j.location}, s.filters, s.tracks, s.search)
        t.add_row(j.title[:40], j.company[:24], j.location[:22], j.posted, f"[dim]{why}[/dim]" if why else "[green]yes[/green]")
    console.print(t)
    if not jobs:
        console.print("[yellow]No postings parsed. Run again with --dump ./teal-pages and send the file "
                      "so the parser can be adjusted.[/yellow]")


alerts_app = typer.Typer(help="Indeed / Glassdoor / LinkedIn job-alert emails (IMAP, read-only)")


@alerts_app.command("test")
def alerts_test(days: int = typer.Option(3, "--days"),
                show_dropped: bool = typer.Option(False, "--dropped", help="also list what the filters drop"),
                dump: Optional[str] = typer.Option(None, "--dump", help="save the raw alert emails (HTML) to this folder")):
    """Read recent alert emails and show which jobs pass your filters (nothing is saved)."""
    from .config import ProjectPaths
    from .filters import filter_reason
    from .settings import load_settings
    from .sources import email_alerts
    s = load_settings(ProjectPaths())
    cfg = s.email_alerts
    jobs = email_alerts.fetch(cfg.get("imap_host", "imap.gmail.com"), cfg.get("folder", "INBOX"),
                              days, cfg.get("senders"), dump_dir=dump)
    kept, dropped = [], []
    for j in jobs:
        why = filter_reason({"title": j.title, "company": j.company, "salary": j.salary,
                             "location": j.location}, s.filters, s.tracks, s.search)
        (dropped if why else kept).append((j, why))
    t = Table(title=f"Alert emails, last {days} days: {len(jobs)} links → {len(kept)} relevant")
    for c in ("Source", "Title", "Company", "Location", "Dropped because" if show_dropped else "Link"):
        t.add_column(c)
    for j, why in kept + (dropped if show_dropped else []):
        t.add_row(j.source, j.title[:40], j.company[:24], j.location[:20],
                  (f"[dim]{why}[/dim]" if why else "") if show_dropped else j.url[:50])
    console.print(t)
