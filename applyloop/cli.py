"""CLI entry point — main interface for applyloop."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console

from .config import find_project_root, import_legacy_env

load_dotenv(find_project_root() / ".env")
import_legacy_env()

app = typer.Typer(
    name="applyloop",
    help="applyloop — find jobs, score fit, tailor your CV, apply, log everything. Start with `applyloop init`.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


def _get_paths():
    from .config import ProjectPaths
    return ProjectPaths()


# ── Pipeline (end-to-end) ────────────────────────────────────────

@app.command()
def run(
    url: Optional[str] = typer.Argument(None, help="Job posting URL"),
    text: Optional[str] = typer.Option(None, "--text", "-t", help="Paste JD text directly"),
    company: str = typer.Option("", "--company", "-c", help="Company name (for pasted text)"),
    title: str = typer.Option("", "--title", help="Job title (for pasted text)"),
    skip_pdf: bool = typer.Option(False, "--skip-pdf", help="Skip PDF generation"),
    skip_tailor: bool = typer.Option(False, "--skip-tailor", help="Skip CV tailoring"),
):
    """Run the full pipeline: extract JD → evaluate → tailor CV → PDF → tracker."""
    from .pipeline import run_pipeline
    result = run_pipeline(
        _get_paths(),
        url=url,
        jd_text=text,
        company=company,
        title=title,
        skip_pdf=skip_pdf,
        skip_tailor=skip_tailor,
    )
    if not result or result.error:
        raise typer.Exit(1)


@app.command()
def evaluate(
    url: Optional[str] = typer.Argument(None, help="Job posting URL"),
    text: Optional[str] = typer.Option(None, "--text", "-t", help="Paste JD text directly"),
    from_scan: bool = typer.Option(False, "--from-scan", help="Evaluate all URLs from scan-history.tsv"),
    date: Optional[str] = typer.Option(None, "--date", "-d", help="Filter scan history by date (YYYY-MM-DD, default: today)"),
    include_evaluated: bool = typer.Option(False, "--include-evaluated", help="Re-evaluate already-evaluated jobs"),
):
    """Evaluate a job (or all scanned jobs) without tailoring CV or generating PDF."""
    from .pipeline import run_pipeline, run_pipeline_batch, load_scan_urls

    if from_scan:
        from datetime import datetime as _dt
        scan_date = date or _dt.now().strftime("%Y-%m-%d")
        urls = load_scan_urls(
            _get_paths(),
            date_filter=scan_date if date else None,
            skip_evaluated=not include_evaluated,
        )
        if not urls:
            console.print("[yellow]No URLs to evaluate from scan history.[/yellow]")
            raise typer.Exit(0)

        console.print(f"[bold]Evaluating {len(urls)} jobs from scan history...[/bold]\n")
        results = run_pipeline_batch(_get_paths(), urls, skip_pdf=True, skip_tailor=True)
        if not results:
            raise typer.Exit(1)
    else:
        result = run_pipeline(
            _get_paths(),
            url=url,
            jd_text=text,
            skip_pdf=True,
            skip_tailor=True,
        )
        if not result or result.error:
            raise typer.Exit(1)


@app.command()
def scan(
    company: Optional[list[str]] = typer.Option(None, "--company", "-c", help="Specific companies to scan"),
    title_filter: Optional[list[str]] = typer.Option(None, "--filter", "-f", help="Title keyword filters"),
):
    """Scan job portals for new listings (reads portals.yml)."""
    from .scanner import scan_portals
    jobs = scan_portals(_get_paths(), companies=company, title_filter=title_filter)
    if not jobs:
        raise typer.Exit(0)
    console.print(f"\n[dim]Run 'applyloop run <url>' on any job to evaluate it.[/dim]")


# ── Find Jobs ─────────────────────────────────────────────────────

@app.command()
def find(
    query: Optional[list[str]] = typer.Option(None, "--query", "-q", help="Search queries (default: target_roles from profile)"),
    location: Optional[str] = typer.Option(None, "--location", "-l", help="Job location filter"),
    source: Optional[list[str]] = typer.Option(None, "--source", "-s", help="Sources: indeed, linkedin, google_jobs, glassdoor"),
    max_results: int = typer.Option(25, "--max", "-m", help="Max results per source"),
    no_portals: bool = typer.Option(False, "--no-portals", help="Skip configured portals"),
    save: Optional[str] = typer.Option(None, "--save", help="Save URLs to file for batch processing"),
):
    """Find jobs matching your profile across multiple job boards."""
    from .job_finder import find_jobs, save_found_jobs

    jobs = find_jobs(
        _get_paths(),
        queries=list(query) if query else None,
        location=location,
        sources=list(source) if source else None,
        max_per_source=max_results,
        include_portals=not no_portals,
    )

    if save and jobs:
        save_found_jobs(jobs, Path(save))

    if not jobs:
        raise typer.Exit(0)
    console.print(f"\n[dim]Run 'applyloop run <url>' to evaluate, or 'applyloop apply <url>' to apply.[/dim]")


# ── Apply ─────────────────────────────────────────────────────────

@app.command()
def apply(
    url: str = typer.Argument(..., help="Job posting URL to apply to"),
    resume: Optional[str] = typer.Option(None, "--resume", "-r", help="Path to resume PDF"),
    auto_submit: bool = typer.Option(False, "--auto-submit", help="Submit without human review"),
    no_cover: bool = typer.Option(False, "--no-cover", help="Skip cover letter generation"),
):
    """Apply to a job — fills the form and pauses for your review."""
    from .applicant import apply_to_job

    resume_path = Path(resume) if resume else None
    result = apply_to_job(
        _get_paths(),
        url=url,
        resume_path=resume_path,
        auto_submit=auto_submit,
        generate_cover=not no_cover,
    )
    if result.status == "failed":
        raise typer.Exit(1)


@app.command("apply-batch")
def apply_batch_cmd(
    file: str = typer.Argument(..., help="File with URLs (one per line)"),
    auto_submit: bool = typer.Option(False, "--auto-submit", help="Submit without human review"),
    no_cover: bool = typer.Option(False, "--no-cover", help="Skip cover letter generation"),
    cooldown: int = typer.Option(30, "--cooldown", help="Seconds between applications"),
):
    """Apply to multiple jobs from a URL file."""
    from .applicant import apply_batch

    url_file = Path(file)
    if not url_file.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    urls = [
        line.split("#")[0].strip()
        for line in url_file.read_text().split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]

    if not urls:
        console.print("[yellow]No URLs found in file[/yellow]")
        raise typer.Exit(0)

    apply_batch(
        _get_paths(),
        urls,
        auto_submit=auto_submit,
        generate_cover=not no_cover,
        cooldown=cooldown,
    )


# ── Autopilot ─────────────────────────────────────────────────────

@app.command()
def autopilot(
    query: Optional[list[str]] = typer.Option(None, "--query", "-q", help="Search queries"),
    location: Optional[str] = typer.Option(None, "--location", "-l", help="Job location"),
    min_score: float = typer.Option(3.5, "--min-score", help="Minimum score to apply (0-5)"),
    max_apps: int = typer.Option(10, "--max-apps", help="Maximum applications per run"),
    auto_submit: bool = typer.Option(False, "--auto-submit", help="Submit without review (use with caution)"),
    no_cover: bool = typer.Option(False, "--no-cover", help="Skip cover letter generation"),
    skip_tailor: bool = typer.Option(False, "--skip-tailor", help="Skip CV tailoring"),
    skip_pdf: bool = typer.Option(False, "--skip-pdf", help="Skip PDF generation"),
    cooldown: int = typer.Option(30, "--cooldown", help="Seconds between applications"),
    source: Optional[list[str]] = typer.Option(None, "--source", "-s", help="Job board sources"),
    no_portals: bool = typer.Option(False, "--no-portals", help="Skip portals"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Find and evaluate only — don't apply"),
):
    """Full autopilot: find jobs → evaluate → tailor CV → apply. Human review by default."""
    from .autopilot import AutopilotConfig, run_autopilot

    cfg = AutopilotConfig(
        min_score=min_score,
        max_applications=max_apps,
        auto_submit=auto_submit,
        generate_cover=not no_cover,
        skip_tailor=skip_tailor,
        skip_pdf=skip_pdf,
        cooldown_seconds=cooldown,
        sources=list(source) if source else ["indeed", "linkedin", "google_jobs", "glassdoor"],
        include_portals=not no_portals,
        queries=list(query) if query else None,
        location=location,
        dry_run=dry_run,
    )

    result = run_autopilot(_get_paths(), config=cfg)

    if result.jobs_submitted == 0 and result.jobs_found > 0:
        raise typer.Exit(1)


@app.command("run-batch")
def run_batch_pipeline(
    file: Optional[str] = typer.Argument(None, help="File with URLs (one per line)"),
    from_scan: bool = typer.Option(False, "--from-scan", help="Use URLs from scan-history.tsv instead of a file"),
    date: Optional[str] = typer.Option(None, "--date", "-d", help="Filter scan history by date (YYYY-MM-DD)"),
    include_evaluated: bool = typer.Option(False, "--include-evaluated", help="Re-evaluate already-evaluated jobs"),
    skip_pdf: bool = typer.Option(False, "--skip-pdf", help="Skip PDF generation"),
    skip_tailor: bool = typer.Option(False, "--skip-tailor", help="Skip CV tailoring"),
):
    """Run the full pipeline for multiple URLs from a file or scan history."""
    from .pipeline import run_pipeline_batch, load_scan_urls

    if from_scan:
        urls = load_scan_urls(
            _get_paths(),
            date_filter=date,
            skip_evaluated=not include_evaluated,
        )
        if not urls:
            console.print("[yellow]No URLs to process from scan history.[/yellow]")
            raise typer.Exit(0)
    elif file:
        from pathlib import Path as P

        url_file = P(file)
        if not url_file.exists():
            console.print(f"[red]File not found: {file}[/red]")
            raise typer.Exit(1)

        urls = [
            line.strip()
            for line in url_file.read_text().split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]

        if not urls:
            console.print("[yellow]No URLs found in file[/yellow]")
            raise typer.Exit(0)
    else:
        console.print("[red]Provide a file or use --from-scan[/red]")
        raise typer.Exit(1)

    run_pipeline_batch(_get_paths(), urls, skip_pdf=skip_pdf, skip_tailor=skip_tailor)


@app.command()
def tailor(
    url: Optional[str] = typer.Argument(None, help="Job posting URL"),
    text: Optional[str] = typer.Option(None, "--text", "-t", help="Paste JD text directly"),
):
    """Tailor your CV for a specific job (no evaluation or tracking)."""
    from .jd_extractor import extract_jd_from_url, extract_jd_from_text
    from .cv_tailor import tailor_cv

    if url:
        jd = extract_jd_from_url(url)
    elif text:
        jd = extract_jd_from_text(text)
    else:
        console.print("[red]Provide a URL or --text[/red]")
        raise typer.Exit(1)

    if not jd.full_text:
        console.print("[red]Could not extract job description[/red]")
        raise typer.Exit(1)

    result = tailor_cv(_get_paths(), jd)
    if not result:
        raise typer.Exit(1)


# ── Doctor ────────────────────────────────────────────────────────

@app.command()
def doctor():
    """Check all prerequisites and print a pass/fail checklist."""
    from .doctor import run_doctor
    exit_code = run_doctor(_get_paths())
    raise typer.Exit(exit_code)


# ── PDF ───────────────────────────────────────────────────────────

@app.command()
def pdf(
    input_file: str = typer.Argument(..., help="Input HTML file"),
    output_file: str = typer.Argument(..., help="Output PDF file"),
    page_format: str = typer.Option("a4", "--format", "-f", help="Page format: a4 or letter"),
):
    """Generate a PDF from an HTML file (ATS-optimized)."""
    from .pdf import generate_pdf
    paths = _get_paths()
    generate_pdf(input_file, output_file, page_format, paths.fonts_dir)


# ── Tracker Commands ──────────────────────────────────────────────

tracker_app = typer.Typer(help="Application tracker management")
app.add_typer(tracker_app, name="tracker")


@tracker_app.command("merge")
def tracker_merge(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
):
    """Merge batch tracker additions into applications.md."""
    from .tracker import merge_tracker
    merge_tracker(_get_paths(), dry_run=dry_run)


@tracker_app.command("normalize")
def tracker_normalize(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
):
    """Normalize all statuses to canonical values."""
    from .tracker import normalize_statuses
    normalize_statuses(_get_paths(), dry_run=dry_run)


@tracker_app.command("dedup")
def tracker_dedup(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
):
    """Remove duplicate entries from applications.md."""
    from .tracker import dedup_tracker
    dedup_tracker(_get_paths(), dry_run=dry_run)


# ── Verify ────────────────────────────────────────────────────────

@app.command()
def verify():
    """Run pipeline integrity checks on applications.md."""
    from .verify import verify_pipeline
    errors, warnings = verify_pipeline(_get_paths())
    raise typer.Exit(1 if errors > 0 else 0)


# ── Sync Check ────────────────────────────────────────────────────

@app.command("sync-check")
def sync_check():
    """Validate CV and profile consistency."""
    from .sync_check import run_sync_check
    errors, _ = run_sync_check(_get_paths())
    raise typer.Exit(1 if errors else 0)


# ── Liveness ──────────────────────────────────────────────────────

@app.command()
def liveness(
    urls: list[str] = typer.Argument(None, help="URLs to check"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="File with URLs (one per line)"),
):
    """Check whether job posting URLs are still active."""
    from .liveness import check_liveness, check_liveness_from_file
    if file:
        results = check_liveness_from_file(file)
    elif urls:
        results = check_liveness(urls)
    else:
        console.print("[red]Provide URLs as arguments or use --file[/red]")
        raise typer.Exit(1)

    expired = sum(1 for r in results if r.status != "active")
    raise typer.Exit(1 if expired > 0 else 0)


# ── Batch ─────────────────────────────────────────────────────────

@app.command()
def batch(
    input_file: Optional[str] = typer.Option(None, "--input", "-i", help="Input TSV file"),
    parallel: int = typer.Option(3, "--parallel", "-p", help="Max parallel jobs"),
    ai_command: str = typer.Option("claude", "--ai-cmd", help="AI CLI command to use"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without executing"),
):
    """Run batch job evaluations in parallel."""
    from .batch import run_batch
    inp = Path(input_file) if input_file else None
    run_batch(_get_paths(), input_file=inp, max_parallel=parallel, ai_command=ai_command, dry_run=dry_run)


# ── Analytics ─────────────────────────────────────────────────────

@app.command()
def analytics():
    """Display pipeline analytics — funnel, scores, time metrics."""
    from .analytics import run_analytics
    run_analytics(_get_paths())


# ── Export ────────────────────────────────────────────────────────

export_app = typer.Typer(help="Export application data")
app.add_typer(export_app, name="export")


@export_app.command("csv")
def export_csv_cmd(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file path"),
):
    """Export applications to CSV."""
    from .export import export_csv
    out = Path(output) if output else None
    export_csv(_get_paths(), output=out)


@export_app.command("json")
def export_json_cmd(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file path"),
):
    """Export applications to JSON."""
    from .export import export_json
    out = Path(output) if output else None
    export_json(_get_paths(), output=out)


@export_app.command("excel")
def export_excel_cmd(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file path"),
):
    """Export applications to Excel (.xlsx)."""
    from .export import export_excel
    out = Path(output) if output else None
    export_excel(_get_paths(), output=out)


# ── Reminders ─────────────────────────────────────────────────────

reminder_app = typer.Typer(help="Follow-up reminder management")
app.add_typer(reminder_app, name="remind")


@reminder_app.command("list")
def reminder_list(
    show_completed: bool = typer.Option(False, "--all", "-a", help="Include completed reminders"),
):
    """List active reminders."""
    from .reminders import list_reminders
    list_reminders(_get_paths(), show_completed=show_completed)


@reminder_app.command("check")
def reminder_check():
    """Check for due or overdue reminders."""
    from .reminders import check_due_reminders
    due = check_due_reminders(_get_paths())
    raise typer.Exit(1 if due else 0)


@reminder_app.command("add")
def reminder_add(
    app_num: int = typer.Argument(..., help="Application number"),
    company: str = typer.Argument(..., help="Company name"),
    role: str = typer.Argument(..., help="Role title"),
    date: str = typer.Argument(..., help="Reminder date (YYYY-MM-DD)"),
    reminder_type: str = typer.Option("follow_up", "--type", "-t", help="Type: follow_up, interview_prep, deadline"),
    message: str = typer.Option("", "--msg", "-m", help="Reminder note"),
):
    """Add a reminder for an application."""
    from .reminders import add_reminder
    add_reminder(_get_paths(), app_num, company, role, date, reminder_type, message)


@reminder_app.command("done")
def reminder_done(
    app_num: int = typer.Argument(..., help="Application number to mark complete"),
):
    """Mark reminders for an application as completed."""
    from .reminders import complete_reminder
    complete_reminder(_get_paths(), app_num)


@reminder_app.command("auto")
def reminder_auto(
    days: int = typer.Option(7, "--days", "-d", help="Days after application to follow up"),
):
    """Auto-generate follow-up reminders for recent applications."""
    from .reminders import auto_generate_follow_ups
    auto_generate_follow_ups(_get_paths(), days_after_apply=days)


# ── Keywords ──────────────────────────────────────────────────────

@app.command()
def keywords(
    jd_file: Optional[str] = typer.Option(None, "--file", "-f", help="Job description file"),
    jd_text: Optional[str] = typer.Option(None, "--text", "-t", help="Job description text"),
):
    """Analyze keyword gaps between a JD and your CV."""
    from .keywords import run_keyword_analysis
    run_keyword_analysis(_get_paths(), jd_path=jd_file, jd_text=jd_text)


# ── Dashboard ─────────────────────────────────────────────────────

@app.command()
def dashboard():
    """Launch the interactive TUI dashboard."""
    from .dashboard.app import run_dashboard
    run_dashboard(_get_paths())


# ── Test ──────────────────────────────────────────────────────────

@app.command()
def test(
    quick: bool = typer.Option(False, "--quick", help="Skip slow checks"),
):
    """Run the built-in test suite."""
    from .test_suite import run_tests
    exit_code = run_tests(_get_paths(), quick=quick)
    raise typer.Exit(exit_code)


from .cli_agent import register as _register_agent  # noqa: E402

_register_agent(app)

if __name__ == "__main__":
    app()
