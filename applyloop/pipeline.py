"""End-to-end pipeline orchestrator — URL in, evaluation + CV + tracker out."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .config import ProjectPaths
from .evaluator import (
    EvaluationResult,
    display_evaluation,
    evaluate_job,
    generate_report_markdown,
)
from .jd_extractor import ExtractedJD, extract_jd_from_text, extract_jd_from_url
from .models import Application

console = Console()


def _next_report_num(paths: ProjectPaths) -> int:
    """Determine the next report number from existing reports."""
    reports_dir = paths.reports_dir
    if not reports_dir.exists():
        reports_dir.mkdir(parents=True, exist_ok=True)
        return 1

    max_num = 0
    for f in reports_dir.glob("*.md"):
        m = re.match(r"^(\d+)", f.stem)
        if m:
            max_num = max(max_num, int(m.group(1)))

    # Also check applications.md for the highest entry number
    apps_file = paths.applications
    if apps_file.exists():
        for line in apps_file.read_text(encoding="utf-8").split("\n"):
            app = Application.from_pipe_line(line)
            if app:
                max_num = max(max_num, app.num)

    return max_num + 1


def _save_report(
    paths: ProjectPaths,
    result: EvaluationResult,
    report_num: int,
) -> Path:
    """Save the evaluation report to reports/ directory."""
    slug = re.sub(r"[^a-z0-9]+", "-", result.company.lower()).strip("-")
    filename = f"{report_num:03d}-{slug}-{result.date}.md"
    report_path = paths.reports_dir / filename
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report_md = generate_report_markdown(result, report_num)
    report_path.write_text(report_md, encoding="utf-8")

    console.print(f"[green]Report saved: {report_path}[/green]")
    return report_path


def _add_tracker_entry(
    paths: ProjectPaths,
    result: EvaluationResult,
    report_num: int,
    report_path: Path,
    pdf_path: Path | None = None,
) -> None:
    """Add an entry to data/applications.md."""
    apps_file = paths.applications
    apps_file.parent.mkdir(parents=True, exist_ok=True)

    if not apps_file.exists():
        apps_file.write_text(
            "# Application Tracker\n\n"
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        )

    pdf_display = pdf_path.name if pdf_path else "—"
    relative_report = report_path.relative_to(paths.root) if report_path else "—"
    report_link = f"[{report_num}]({relative_report})"

    new_line = (
        f"| {report_num} | {result.date} | {result.company} | {result.role} "
        f"| {result.score_display} | {result.status} "
        f"| {pdf_display} | {report_link} | {result.recommendation.upper()} |"
    )

    content = apps_file.read_text(encoding="utf-8")
    lines = content.split("\n")

    insert_idx = -1
    for i, line in enumerate(lines):
        if "---" in line and line.startswith("|"):
            insert_idx = i + 1
            break

    if insert_idx >= 0:
        lines.insert(insert_idx, new_line)
    else:
        lines.append(new_line)

    apps_file.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]Tracker updated: entry #{report_num}[/green]")


def run_pipeline(
    paths: ProjectPaths,
    url: str | None = None,
    jd_text: str | None = None,
    company: str = "",
    title: str = "",
    skip_pdf: bool = False,
    skip_tailor: bool = False,
) -> EvaluationResult | None:
    """
    Run the full end-to-end pipeline.

    Flow:
    1. Extract JD (from URL via Playwright, or from pasted text)
    2. Evaluate the job against CV + profile using AI
    3. Generate evaluation report
    4. Tailor CV for this specific job
    5. Generate PDF from tailored CV
    6. Add entry to application tracker

    Returns the EvaluationResult, or None on failure.
    """
    console.print(Panel("[bold]applyloop pipeline[/bold]", style="cyan"))

    # -- Step 1: Extract job description
    console.print("\n[bold]Step 1: Extract Job Description[/bold]")

    jd: ExtractedJD
    if url:
        jd = extract_jd_from_url(url)
    elif jd_text:
        jd = extract_jd_from_text(jd_text, company=company, title=title)
    else:
        console.print("[red]Provide either a URL or JD text[/red]")
        return None

    if jd.error and not jd.full_text:
        console.print(f"[red]Failed to extract JD: {jd.error}[/red]")
        return None

    if not jd.is_active:
        console.print(f"[yellow]Job posting appears expired: {jd.error}[/yellow]")
        return None

    if jd.company and company:
        jd.company = company
    if jd.title and title:
        jd.title = title

    # -- Step 2: AI Evaluation
    console.print("\n[bold]Step 2: Evaluate Job Fit[/bold]")

    result = evaluate_job(jd, paths)
    if result.error:
        console.print(f"[red]Evaluation failed: {result.error}[/red]")
        return result

    display_evaluation(result)

    # -- Step 3: Save report
    console.print("\n[bold]Step 3: Save Report[/bold]")

    report_num = _next_report_num(paths)
    report_path = _save_report(paths, result, report_num)

    # -- Step 4: Tailor CV (optional)
    pdf_path = None

    if not skip_tailor:
        console.print("\n[bold]Step 4: Tailor CV[/bold]")

        from .cv_tailor import tailor_cv, tailor_cv_to_html

        tailored_md = tailor_cv(paths, jd, evaluation=result)

        if tailored_md and not skip_pdf:
            console.print("\n[bold]Step 5: Generate PDF[/bold]")

            html_path = tailor_cv_to_html(tailored_md, paths)
            if html_path:
                try:
                    from .pdf import generate_pdf

                    slug = re.sub(r"[^a-z0-9]+", "-", result.company.lower()).strip("-")
                    pdf_out = paths.output_dir / f"cv-{slug}-{result.date}.pdf"
                    generate_pdf(str(html_path), str(pdf_out), fonts_dir=paths.fonts_dir)
                    pdf_path = pdf_out
                except Exception as e:
                    console.print(f"[yellow]PDF generation skipped: {e}[/yellow]")
    else:
        console.print("\n[dim]Steps 4-5: CV tailoring skipped[/dim]")

    # -- Step 6: Update tracker
    console.print("\n[bold]Step 6: Update Tracker[/bold]")

    _add_tracker_entry(paths, result, report_num, report_path, pdf_path)

    # -- Summary
    grade_color = (
        "green" if result.overall_score >= 4.0
        else "yellow" if result.overall_score >= 3.0
        else "red"
    )
    console.print(Panel(
        f"[bold]{result.company} — {result.role}[/bold]\n"
        f"Score: [{grade_color}]{result.score_display} (Grade {result.grade})[/{grade_color}]\n"
        f"Recommendation: {result.recommendation.upper()}\n"
        f"Report: {report_path}\n"
        f"PDF: {pdf_path or 'skipped'}",
        title="Pipeline Complete",
        style="green",
    ))

    return result


def load_scan_urls(
    paths: ProjectPaths,
    date_filter: str | None = None,
    skip_evaluated: bool = True,
) -> list[str]:
    """
    Read URLs from scan-history.tsv.

    Args:
        date_filter: Only include URLs from this date (YYYY-MM-DD). None = all.
        skip_evaluated: If True, exclude URLs already present in applications.md.
    """
    history_file = paths.scan_history
    if not history_file.exists():
        console.print("[yellow]No scan history found. Run 'applyloop find' first.[/yellow]")
        return []

    urls: list[str] = []
    for line in history_file.read_text(encoding="utf-8").strip().split("\n"):
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        url = parts[0].strip()
        scan_date = parts[1].strip() if len(parts) > 1 else ""

        if not url:
            continue
        if date_filter and scan_date != date_filter:
            continue
        urls.append(url)

    if skip_evaluated:
        already = _already_evaluated_urls(paths)
        before = len(urls)
        urls = [u for u in urls if u not in already]
        skipped = before - len(urls)
        if skipped:
            console.print(f"[dim]Skipped {skipped} already-evaluated URLs[/dim]")

    return urls


def _already_evaluated_urls(paths: ProjectPaths) -> set[str]:
    """Collect URLs that already appear in reports/ so we don't re-evaluate."""
    seen: set[str] = set()

    apps_file = paths.applications
    if apps_file.exists():
        for line in apps_file.read_text(encoding="utf-8").split("\n"):
            app = Application.from_pipe_line(line)
            if app and app.notes:
                url_match = re.search(r"https?://\S+", app.notes)
                if url_match:
                    seen.add(url_match.group(0))

    for report in paths.reports_dir.glob("*.md"):
        try:
            for line in report.read_text(encoding="utf-8").split("\n")[:10]:
                if line.startswith("**URL:**"):
                    url = line.replace("**URL:**", "").strip()
                    if url:
                        seen.add(url)
        except Exception:
            pass

    return seen


def run_pipeline_batch(
    paths: ProjectPaths,
    urls: list[str],
    skip_pdf: bool = False,
    skip_tailor: bool = False,
) -> list[EvaluationResult]:
    """
    Run the pipeline for multiple URLs sequentially.

    Returns list of EvaluationResults.
    """
    results: list[EvaluationResult] = []

    console.print(f"[bold]Batch pipeline: {len(urls)} jobs[/bold]\n")

    for i, url in enumerate(urls, 1):
        console.print(f"\n{'=' * 60}")
        console.print(f"[bold cyan]Job {i}/{len(urls)}[/bold cyan]")
        console.print(f"{'=' * 60}")

        result = run_pipeline(
            paths,
            url=url,
            skip_pdf=skip_pdf,
            skip_tailor=skip_tailor,
        )
        if result:
            results.append(result)

    # Summary table
    if results:
        from rich.table import Table

        console.print(f"\n{'=' * 60}")
        console.print("[bold]Batch Summary[/bold]\n")

        table = Table(show_header=True, header_style="bold")
        table.add_column("#", justify="right")
        table.add_column("Company")
        table.add_column("Role")
        table.add_column("Score", justify="center")
        table.add_column("Grade", justify="center")
        table.add_column("Action")

        for r in results:
            sc = "green" if r.overall_score >= 4.0 else "yellow" if r.overall_score >= 3.0 else "red"
            rc = {"apply": "green", "consider": "yellow", "skip": "red"}.get(r.recommendation, "white")
            table.add_row(
                str(results.index(r) + 1),
                r.company,
                r.role,
                f"[{sc}]{r.score_display}[/{sc}]",
                r.grade,
                f"[{rc}]{r.recommendation.upper()}[/{rc}]",
            )

        console.print(table)
        apply_count = sum(1 for r in results if r.recommendation == "apply")
        console.print(f"\n[bold]Worth applying: {apply_count}/{len(results)}[/bold]")

    return results
