"""Export application data to CSV, JSON, or Excel."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from rich.console import Console

from .config import ProjectPaths
from .models import Application

console = Console()


def _load_entries(paths: ProjectPaths) -> list[Application]:
    apps_file = paths.applications
    if not apps_file.exists():
        return []
    entries = []
    for line in apps_file.read_text(encoding="utf-8").split("\n"):
        if not line.startswith("|"):
            continue
        app = Application.from_pipe_line(line)
        if app:
            entries.append(app)
    return entries


def _entry_to_dict(e: Application) -> dict:
    return {
        "num": e.num,
        "date": e.date,
        "company": e.company,
        "role": e.role,
        "score": e.score,
        "status": e.status,
        "pdf": e.pdf,
        "report": e.report,
        "notes": e.notes,
        "canonical_status": e.canonical_status.value,
        "score_numeric": e.parsed_score,
    }


def export_csv(paths: ProjectPaths, output: Path | None = None) -> Path:
    """Export applications to CSV."""
    entries = _load_entries(paths)
    out = output or (paths.output_dir / "applications.csv")
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "num", "date", "company", "role", "score", "score_numeric",
                "status", "canonical_status", "pdf", "report", "notes",
            ],
        )
        writer.writeheader()
        for e in entries:
            writer.writerow(_entry_to_dict(e))

    console.print(f"[green]Exported {len(entries)} entries to {out}[/green]")
    return out


def export_json(paths: ProjectPaths, output: Path | None = None) -> Path:
    """Export applications to JSON."""
    entries = _load_entries(paths)
    out = output or (paths.output_dir / "applications.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "exported_at": __import__("datetime").datetime.now().isoformat(),
        "total": len(entries),
        "entries": [_entry_to_dict(e) for e in entries],
    }

    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[green]Exported {len(entries)} entries to {out}[/green]")
    return out


def export_excel(paths: ProjectPaths, output: Path | None = None) -> Path:
    """Export applications to Excel (.xlsx)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        console.print("[red]openpyxl not installed. Run: pip install openpyxl[/red]")
        raise

    entries = _load_entries(paths)
    out = output or (paths.output_dir / "applications.xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Applications"

    headers = [
        "#", "Date", "Company", "Role", "Score", "Score (Numeric)",
        "Status", "Canonical Status", "PDF", "Report", "Notes",
    ]

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    status_colors = {
        "Evaluated": "FFF2CC",
        "Applied": "D9E2F3",
        "Responded": "E2EFDA",
        "Interview": "DAEEF3",
        "Offer": "C6EFCE",
        "Rejected": "FFC7CE",
        "Discarded": "D9D9D9",
        "SKIP": "F2F2F2",
    }

    for row, e in enumerate(entries, 2):
        d = _entry_to_dict(e)
        ws.cell(row=row, column=1, value=d["num"])
        ws.cell(row=row, column=2, value=d["date"])
        ws.cell(row=row, column=3, value=d["company"])
        ws.cell(row=row, column=4, value=d["role"])
        ws.cell(row=row, column=5, value=d["score"])
        ws.cell(row=row, column=6, value=d["score_numeric"])
        ws.cell(row=row, column=7, value=d["status"])
        ws.cell(row=row, column=8, value=d["canonical_status"])
        ws.cell(row=row, column=9, value=d["pdf"])
        ws.cell(row=row, column=10, value=d["report"])
        ws.cell(row=row, column=11, value=d["notes"])

        color = status_colors.get(d["canonical_status"])
        if color:
            fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
            for col in range(1, 12):
                ws.cell(row=row, column=col).fill = fill

    for col in range(1, 12):
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = 18

    wb.save(str(out))
    console.print(f"[green]Exported {len(entries)} entries to {out}[/green]")
    return out
