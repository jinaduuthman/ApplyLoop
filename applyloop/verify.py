"""Pipeline verification — health checks for tracker data integrity."""

from __future__ import annotations

import re
from pathlib import Path

from rich.console import Console

from .config import ProjectPaths
from .models import Application, ApplicationStatus

console = Console()

CANONICAL_STATUSES = {s.value.lower() for s in ApplicationStatus}

ALIASES = {
    "enviada": "applied",
    "aplicada": "applied",
    "sent": "applied",
    "cerrada": "discarded",
    "descartada": "discarded",
    "cancelada": "discarded",
    "rechazada": "rejected",
    "no_aplicar": "skip",
    "monitor": "skip",
    "evaluada": "evaluated",
    "aplicado": "applied",
    "respondido": "responded",
    "entrevista": "interview",
    "oferta": "offer",
    "rechazado": "rejected",
    "descartado": "discarded",
    "no aplicar": "skip",
}


def verify_pipeline(paths: ProjectPaths) -> tuple[int, int]:
    """
    Run pipeline integrity checks on applications.md.

    Returns (errors, warnings).
    """
    apps_file = paths.applications
    additions_dir = paths.tracker_additions_dir
    reports_dir = paths.reports_dir

    errors = 0
    warnings = 0

    def error(msg: str) -> None:
        nonlocal errors
        console.print(f"[red]  ✗ {msg}[/red]")
        errors += 1

    def warn(msg: str) -> None:
        nonlocal warnings
        console.print(f"[yellow]  ! {msg}[/yellow]")
        warnings += 1

    def ok(msg: str) -> None:
        console.print(f"[green]  ✓ {msg}[/green]")

    if not apps_file.exists():
        console.print(
            "\n[dim]No applications.md found. This is normal for a fresh setup.[/dim]"
        )
        console.print(
            "[dim]The file will be created when you evaluate your first offer.[/dim]\n"
        )
        return 0, 0

    content = apps_file.read_text(encoding="utf-8")
    lines = content.split("\n")

    entries: list[Application] = []
    for line in lines:
        if not line.startswith("|"):
            continue
        app = Application.from_pipe_line(line)
        if app:
            entries.append(app)

    console.print(f"\n[bold]Checking {len(entries)} entries in applications.md[/bold]\n")

    # -- Check 1: Canonical statuses
    bad_statuses = 0
    for e in entries:
        cleaned = re.sub(r"\*\*", "", e.status).strip().lower()
        status_only = re.sub(r"\s+\d{4}-\d{2}-\d{2}.*$", "", cleaned).strip()

        if status_only not in CANONICAL_STATUSES and status_only not in ALIASES:
            error(f'#{e.num}: Non-canonical status "{e.status}"')
            bad_statuses += 1

        if "**" in e.status:
            error(f'#{e.num}: Status contains markdown bold: "{e.status}"')
            bad_statuses += 1

        if re.search(r"\d{4}-\d{2}-\d{2}", e.status):
            error(f'#{e.num}: Status contains date: "{e.status}" — dates go in date column')
            bad_statuses += 1

    if bad_statuses == 0:
        ok("All statuses are canonical")

    # -- Check 2: Duplicates
    company_role_map: dict[str, list[Application]] = {}
    dupes = 0
    for e in entries:
        key = (
            re.sub(r"[^a-z0-9]", "", e.company.lower())
            + "::"
            + re.sub(r"[^a-z0-9 ]", "", e.role.lower())
        )
        company_role_map.setdefault(key, []).append(e)

    for key, group in company_role_map.items():
        if len(group) > 1:
            nums = ", ".join(f"#{e.num}" for e in group)
            warn(
                f"Possible duplicates: {nums} "
                f"({group[0].company} — {group[0].role})"
            )
            dupes += 1

    if dupes == 0:
        ok("No exact duplicates found")

    # -- Check 3: Report links
    broken_reports = 0
    for e in entries:
        m = re.search(r"\]\(([^)]+)\)", e.report)
        if not m:
            continue
        report_path = paths.root / m.group(1)
        if not report_path.exists():
            error(f"#{e.num}: Report not found: {m.group(1)}")
            broken_reports += 1

    if broken_reports == 0:
        ok("All report links valid")

    # -- Check 4: Score format
    bad_scores = 0
    for e in entries:
        s = e.score.replace("**", "").strip()
        if not re.match(r"^\d+\.?\d*/5$", s) and s not in ("N/A", "DUP"):
            error(f'#{e.num}: Invalid score format: "{e.score}"')
            bad_scores += 1

    if bad_scores == 0:
        ok("All scores valid")

    # -- Check 5: Row format
    bad_rows = 0
    for line in lines:
        if not line.startswith("|"):
            continue
        if "---" in line or "Empresa" in line:
            continue
        parts = line.split("|")
        if len(parts) < 9:
            error(f"Row with <9 columns: {line[:80]}...")
            bad_rows += 1

    if bad_rows == 0:
        ok("All rows properly formatted")

    # -- Check 6: Pending TSVs
    pending_tsvs = 0
    if additions_dir.exists():
        pending_tsvs = len([f for f in additions_dir.iterdir() if f.suffix == ".tsv"])
        if pending_tsvs > 0:
            warn(f"{pending_tsvs} pending TSVs in tracker-additions/ (not merged)")

    if pending_tsvs == 0:
        ok("No pending TSVs")

    # -- Check 7: Bold in scores
    bold_scores = 0
    for e in entries:
        if "**" in e.score:
            warn(f'#{e.num}: Score has markdown bold: "{e.score}"')
            bold_scores += 1

    if bold_scores == 0:
        ok("No bold in scores")

    # -- Summary
    console.print("\n" + "=" * 50)
    console.print(f"[bold]Pipeline Health: {errors} errors, {warnings} warnings[/bold]")
    if errors == 0 and warnings == 0:
        console.print("[green]Pipeline is clean![/green]")
    elif errors == 0:
        console.print("[yellow]Pipeline OK with warnings[/yellow]")
    else:
        console.print("[red]Pipeline has errors — fix before proceeding[/red]")

    return errors, warnings
