"""Follow-up reminder system for tracked applications."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import ProjectPaths
from .models import Application, Reminder

console = Console()

DEFAULT_FOLLOW_UP_DAYS = 7
DEFAULT_INTERVIEW_PREP_DAYS = 1


def _load_reminders(paths: ProjectPaths) -> list[Reminder]:
    rfile = paths.reminders_file
    if not rfile.exists():
        return []
    data = json.loads(rfile.read_text(encoding="utf-8"))
    return [Reminder.from_dict(r) for r in data]


def _save_reminders(paths: ProjectPaths, reminders: list[Reminder]) -> None:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    rfile = paths.reminders_file
    rfile.write_text(
        json.dumps([r.to_dict() for r in reminders], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def add_reminder(
    paths: ProjectPaths,
    application_num: int,
    company: str,
    role: str,
    remind_date: str,
    reminder_type: str = "follow_up",
    message: str = "",
) -> Reminder:
    """Add a new reminder for an application."""
    reminders = _load_reminders(paths)
    reminder = Reminder(
        application_num=application_num,
        company=company,
        role=role,
        remind_date=remind_date,
        reminder_type=reminder_type,
        message=message,
    )
    reminders.append(reminder)
    _save_reminders(paths, reminders)
    console.print(
        f"[green]Reminder set for #{application_num} ({company}) on {remind_date}[/green]"
    )
    return reminder


def complete_reminder(paths: ProjectPaths, application_num: int) -> bool:
    """Mark all reminders for an application as completed."""
    reminders = _load_reminders(paths)
    found = False
    for r in reminders:
        if r.application_num == application_num and not r.completed:
            r.completed = True
            found = True
    if found:
        _save_reminders(paths, reminders)
        console.print(f"[green]Reminders for #{application_num} marked complete[/green]")
    else:
        console.print(f"[yellow]No active reminders found for #{application_num}[/yellow]")
    return found


def check_due_reminders(paths: ProjectPaths) -> list[Reminder]:
    """Check for reminders due today or overdue."""
    reminders = _load_reminders(paths)
    today = date.today().isoformat()
    due = [r for r in reminders if not r.completed and r.remind_date <= today]

    if not due:
        console.print("[green]No due reminders.[/green]")
        return due

    console.print(f"\n[bold yellow]You have {len(due)} due reminder(s):[/bold yellow]\n")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Due", style="red")
    table.add_column("Type")
    table.add_column("Note")

    for r in due:
        table.add_row(
            str(r.application_num),
            r.company,
            r.role,
            r.remind_date,
            r.reminder_type,
            r.message,
        )

    console.print(table)
    return due


def auto_generate_follow_ups(
    paths: ProjectPaths,
    days_after_apply: int = DEFAULT_FOLLOW_UP_DAYS,
) -> int:
    """Auto-generate follow-up reminders for recent applications without one."""
    apps_file = paths.applications
    if not apps_file.exists():
        return 0

    entries = []
    for line in apps_file.read_text(encoding="utf-8").split("\n"):
        if not line.startswith("|"):
            continue
        app = Application.from_pipe_line(line)
        if app:
            entries.append(app)

    existing = _load_reminders(paths)
    existing_nums = {r.application_num for r in existing if not r.completed}

    created = 0
    today = date.today()

    for e in entries:
        if e.num in existing_nums:
            continue

        status = e.canonical_status.value.lower()
        if status not in ("applied", "responded"):
            continue

        app_date = e.parsed_date
        if not app_date:
            continue

        follow_up_date = app_date + timedelta(days=days_after_apply)
        if follow_up_date < today:
            follow_up_date = today + timedelta(days=1)

        add_reminder(
            paths,
            application_num=e.num,
            company=e.company,
            role=e.role,
            remind_date=follow_up_date.isoformat(),
            reminder_type="follow_up",
            message=f"Follow up on application submitted {e.date}",
        )
        created += 1

    if created:
        console.print(f"\n[green]Created {created} follow-up reminders[/green]")
    else:
        console.print("[dim]No new follow-up reminders needed[/dim]")

    return created


def list_reminders(paths: ProjectPaths, show_completed: bool = False) -> list[Reminder]:
    """List all reminders."""
    reminders = _load_reminders(paths)
    if not show_completed:
        reminders = [r for r in reminders if not r.completed]

    if not reminders:
        console.print("[dim]No active reminders.[/dim]")
        return reminders

    table = Table(show_header=True, header_style="bold", title="Reminders")
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Due Date")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Note")

    for r in sorted(reminders, key=lambda x: x.remind_date):
        status_style = "green" if r.completed else ("red" if r.remind_date <= date.today().isoformat() else "yellow")
        table.add_row(
            str(r.application_num),
            r.company,
            r.role,
            r.remind_date,
            r.reminder_type,
            f"[{status_style}]{'Done' if r.completed else 'Pending'}[/{status_style}]",
            r.message,
        )

    console.print(table)
    return reminders
