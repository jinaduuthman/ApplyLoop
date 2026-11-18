"""Autopilot — full-loop automation: find jobs → evaluate → tailor CV → apply.

Orchestrates the entire job search and application pipeline with
configurable thresholds and safety controls.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from .applicant import ApplicationResult, apply_to_job, save_application_log
from .config import ProjectPaths, load_profile
from .evaluator import EvaluationResult
from .jd_extractor import ExtractedJD, extract_jd_from_url
from .job_finder import FoundJob, find_jobs, save_found_jobs
from .pipeline import run_pipeline

console = Console()


@dataclass
class AutopilotConfig:
    min_score: float = 3.5
    max_applications: int = 10
    auto_submit: bool = False
    generate_cover: bool = True
    skip_tailor: bool = False
    skip_pdf: bool = False
    cooldown_seconds: int = 30
    sources: list[str] = field(default_factory=lambda: ["indeed", "linkedin", "google_jobs", "glassdoor"])
    include_portals: bool = True
    queries: list[str] | None = None
    location: str | None = None
    max_per_source: int = 25
    dry_run: bool = False

    @classmethod
    def from_kwargs(cls, **kwargs) -> "AutopilotConfig":
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in kwargs.items() if k in valid_fields and v is not None}
        return cls(**filtered)


@dataclass
class AutopilotResult:
    jobs_found: int = 0
    jobs_evaluated: int = 0
    jobs_above_threshold: int = 0
    jobs_applied: int = 0
    jobs_submitted: int = 0
    jobs_failed: int = 0
    jobs_skipped: int = 0
    evaluations: list[EvaluationResult] = field(default_factory=list)
    applications: list[ApplicationResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


def run_autopilot(
    paths: ProjectPaths,
    config: AutopilotConfig | None = None,
) -> AutopilotResult:
    """
    Run the full autopilot loop:
    1. Find jobs matching the candidate's profile
    2. Evaluate each job with AI
    3. For jobs above the score threshold, tailor CV and optionally apply

    Returns an AutopilotResult with metrics and detailed results.
    """
    if config is None:
        config = AutopilotConfig()

    result = AutopilotResult(started_at=datetime.now().isoformat())

    console.print(Panel(
        "[bold]applyloop autopilot[/bold]\n\n"
        f"Min score: {config.min_score}/5\n"
        f"Max applications: {config.max_applications}\n"
        f"Auto-submit: {'[red]YES[/red]' if config.auto_submit else '[green]NO (human review)[/green]'}\n"
        f"Sources: {', '.join(config.sources)}\n"
        f"Dry run: {'yes' if config.dry_run else 'no'}",
        title="Configuration",
        style="cyan",
    ))

    if config.auto_submit and not config.dry_run:
        console.print(
            "\n[bold red]WARNING: Auto-submit is ON. Applications will be submitted "
            "without review.[/bold red]"
        )
        if not Confirm.ask("Continue with auto-submit?", default=False):
            console.print("[dim]Autopilot cancelled[/dim]")
            return result

    # ── Step 1: Find jobs ─────────────────────────────────────────
    console.print(f"\n{'═' * 60}")
    console.print("[bold]Step 1: Find Jobs[/bold]")
    console.print(f"{'═' * 60}\n")

    found_jobs = find_jobs(
        paths,
        queries=config.queries,
        location=config.location,
        sources=config.sources,
        max_per_source=config.max_per_source,
        include_portals=config.include_portals,
    )

    result.jobs_found = len(found_jobs)

    if not found_jobs:
        console.print("[yellow]No jobs found. Check your search criteria.[/yellow]")
        result.finished_at = datetime.now().isoformat()
        return result

    jobs_with_urls = [j for j in found_jobs if j.url]
    console.print(f"\n[bold]{len(jobs_with_urls)} jobs with URLs to evaluate[/bold]")

    if config.dry_run:
        save_path = paths.data_dir / f"found-jobs-{datetime.now().strftime('%Y%m%d-%H%M')}.txt"
        save_found_jobs(found_jobs, save_path)
        console.print(f"\n[dim]Dry run — saved {len(found_jobs)} jobs to {save_path}[/dim]")
        result.finished_at = datetime.now().isoformat()
        return result

    # ── Step 2: Evaluate ──────────────────────────────────────────
    console.print(f"\n{'═' * 60}")
    console.print("[bold]Step 2: Evaluate Jobs[/bold]")
    console.print(f"{'═' * 60}\n")

    worthy_jobs: list[tuple[FoundJob, EvaluationResult]] = []

    for i, job in enumerate(jobs_with_urls, 1):
        if len(worthy_jobs) >= config.max_applications:
            console.print(
                f"\n[yellow]Reached max applications limit ({config.max_applications})[/yellow]"
            )
            break

        console.print(f"\n[bold cyan]Evaluating {i}/{len(jobs_with_urls)}: "
                       f"{job.company} — {job.title}[/bold cyan]")

        try:
            eval_result = run_pipeline(
                paths,
                url=job.url,
                skip_pdf=True,
                skip_tailor=True,
            )
        except Exception as e:
            console.print(f"[red]Evaluation failed: {e}[/red]")
            continue

        result.jobs_evaluated += 1

        if eval_result and not eval_result.error:
            result.evaluations.append(eval_result)
            job.score = eval_result.overall_score

            if eval_result.overall_score >= config.min_score:
                result.jobs_above_threshold += 1
                worthy_jobs.append((job, eval_result))
                console.print(
                    f"  [green]✓ Score {eval_result.score_display} — "
                    f"above threshold ({config.min_score})[/green]"
                )
            else:
                console.print(
                    f"  [dim]✗ Score {eval_result.score_display} — "
                    f"below threshold ({config.min_score})[/dim]"
                )

        time.sleep(2)

    if not worthy_jobs:
        console.print("\n[yellow]No jobs scored above the threshold.[/yellow]")
        _print_autopilot_summary(result)
        result.finished_at = datetime.now().isoformat()
        return result

    console.print(f"\n[bold green]{len(worthy_jobs)} jobs above threshold:[/bold green]")
    for job, ev in worthy_jobs:
        console.print(f"  • {ev.company} — {ev.role} ({ev.score_display})")

    # ── Step 3: Tailor & Apply ────────────────────────────────────
    console.print(f"\n{'═' * 60}")
    console.print("[bold]Step 3: Tailor CV & Apply[/bold]")
    console.print(f"{'═' * 60}\n")

    for i, (job, evaluation) in enumerate(worthy_jobs, 1):
        console.print(f"\n[bold cyan]Application {i}/{len(worthy_jobs)}: "
                       f"{evaluation.company} — {evaluation.role}[/bold cyan]")

        result.jobs_applied += 1

        resume_path: Path | None = None
        if not config.skip_tailor:
            console.print("[dim]Tailoring CV...[/dim]")
            try:
                jd = extract_jd_from_url(job.url)

                from .cv_tailor import tailor_cv, tailor_cv_to_html

                tailored_md = tailor_cv(paths, jd, evaluation=evaluation)

                if tailored_md and not config.skip_pdf:
                    html_path = tailor_cv_to_html(tailored_md, paths)
                    if html_path:
                        import re as _re
                        from .pdf import generate_pdf

                        slug = _re.sub(r"[^a-z0-9]+", "-", evaluation.company.lower()).strip("-")
                        pdf_out = paths.output_dir / f"cv-{slug}-{evaluation.date}.pdf"
                        try:
                            generate_pdf(str(html_path), str(pdf_out), fonts_dir=paths.fonts_dir)
                            resume_path = pdf_out
                        except Exception as e:
                            console.print(f"[yellow]PDF failed: {e}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]CV tailoring failed: {e}[/yellow]")

        console.print("[dim]Filling application form...[/dim]")
        jd_for_apply = ExtractedJD(
            url=job.url,
            title=evaluation.role,
            company=evaluation.company,
            full_text=evaluation.summary,
        )

        app_result = apply_to_job(
            paths,
            url=job.url,
            jd=jd_for_apply,
            resume_path=resume_path,
            auto_submit=config.auto_submit,
            generate_cover=config.generate_cover,
        )

        result.applications.append(app_result)

        if app_result.status == "submitted":
            result.jobs_submitted += 1
        elif app_result.status == "failed":
            result.jobs_failed += 1
        elif app_result.status == "skipped":
            result.jobs_skipped += 1

        if i < len(worthy_jobs):
            console.print(f"\n[dim]Cooldown: {config.cooldown_seconds}s...[/dim]")
            time.sleep(config.cooldown_seconds)

    # Save application log
    log_path = paths.data_dir / "application-log.json"
    save_application_log(result.applications, log_path)

    result.finished_at = datetime.now().isoformat()
    _print_autopilot_summary(result)

    return result


def _print_autopilot_summary(result: AutopilotResult) -> None:
    """Display the autopilot run summary."""
    console.print(f"\n{'═' * 60}")

    table = Table(
        title="Autopilot Summary",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")

    table.add_row("Jobs Found", str(result.jobs_found))
    table.add_row("Jobs Evaluated", str(result.jobs_evaluated))
    table.add_row("Above Threshold", f"[green]{result.jobs_above_threshold}[/green]")
    table.add_row("Applied", str(result.jobs_applied))
    table.add_row("Submitted", f"[bold green]{result.jobs_submitted}[/bold green]")
    table.add_row("Failed", f"[red]{result.jobs_failed}[/red]" if result.jobs_failed else "0")
    table.add_row("Skipped", str(result.jobs_skipped))

    console.print(table)

    if result.evaluations:
        console.print("\n[bold]Top Evaluated Jobs:[/bold]")
        sorted_evals = sorted(result.evaluations, key=lambda e: e.overall_score, reverse=True)
        for ev in sorted_evals[:5]:
            sc = "green" if ev.overall_score >= 4.0 else "yellow" if ev.overall_score >= 3.0 else "red"
            console.print(f"  [{sc}]{ev.score_display}[/{sc}] {ev.company} — {ev.role}")

    console.print()
