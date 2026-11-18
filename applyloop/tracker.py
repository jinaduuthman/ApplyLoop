"""Application tracker management — merge, dedup, normalize."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from rich.console import Console

from .config import ProjectPaths
from .models import Application, ApplicationStatus, role_fuzzy_match

console = Console()


# ---------------------------------------------------------------------------
# Merge: batch/tracker-additions/*.tsv → data/applications.md
# ---------------------------------------------------------------------------


def _detect_tsv_columns(parts: list[str]) -> tuple[str, str]:
    """Detect whether cols 4/5 are (status, score) or (score, status)."""
    col4 = parts[4].strip()
    col5 = parts[5].strip()

    score_pat = re.compile(r"^\d+\.?\d*/5$")
    status_keywords = re.compile(
        r"^(evaluated|applied|responded|interview|offer|rejected|discarded|skip|"
        r"evaluada|aplicado|respondido|entrevista|oferta|rechazado|descartado|"
        r"no aplicar|cerrada|duplicado|repost|condicional|hold|monitor)",
        re.IGNORECASE,
    )

    c4_score = bool(score_pat.match(col4)) or col4 in ("N/A", "DUP")
    c5_score = bool(score_pat.match(col5)) or col5 in ("N/A", "DUP")
    c4_status = bool(status_keywords.match(col4))
    c5_status = bool(status_keywords.match(col5))

    if c4_status and not c4_score:
        return col4, col5
    if c4_score and c5_status:
        return col5, col4
    if c5_score and not c4_score:
        return col4, col5
    return col4, col5


def parse_tsv_content(content: str, filename: str) -> Optional[dict]:
    """Parse a TSV file into a structured addition dict."""
    content = content.strip()
    if not content:
        return None

    if content.startswith("|"):
        parts = [s.strip() for s in content.split("|") if s.strip()]
        if len(parts) < 8:
            console.print(f"[yellow]Skipping malformed pipe-delimited {filename}: {len(parts)} fields[/yellow]")
            return None
        status_str = ApplicationStatus.from_string(parts[5]).value
        return {
            "num": int(parts[0]),
            "date": parts[1],
            "company": parts[2],
            "role": parts[3],
            "score": parts[4],
            "status": status_str,
            "pdf": parts[6],
            "report": parts[7],
            "notes": parts[8] if len(parts) > 8 else "",
        }

    parts = content.split("\t")
    if len(parts) < 8:
        console.print(f"[yellow]Skipping malformed TSV {filename}: {len(parts)} fields[/yellow]")
        return None

    status_col, score_col = _detect_tsv_columns(parts)
    status_str = ApplicationStatus.from_string(status_col).value

    try:
        num = int(parts[0])
    except ValueError:
        console.print(f"[yellow]Skipping {filename}: invalid entry number[/yellow]")
        return None

    if num == 0:
        return None

    return {
        "num": num,
        "date": parts[1],
        "company": parts[2],
        "role": parts[3],
        "status": status_str,
        "score": score_col,
        "pdf": parts[6],
        "report": parts[7],
        "notes": parts[8] if len(parts) > 8 else "",
    }


def merge_tracker(paths: ProjectPaths, dry_run: bool = False) -> dict:
    """
    Merge batch/tracker-additions/*.tsv into applications.md.

    Returns summary: {added, updated, skipped}.
    """
    apps_file = paths.applications
    additions_dir = paths.tracker_additions_dir
    merged_dir = paths.merged_dir

    if not apps_file.exists():
        console.print("No applications.md found. Nothing to merge into.")
        return {"added": 0, "updated": 0, "skipped": 0}

    lines = apps_file.read_text(encoding="utf-8").split("\n")

    existing: list[Application] = []
    max_num = 0
    for line in lines:
        if line.startswith("|") and "---" not in line and "Empresa" not in line:
            app = Application.from_pipe_line(line)
            if app:
                existing.append(app)
                max_num = max(max_num, app.num)

    console.print(f"[dim]Existing: {len(existing)} entries, max #{max_num}[/dim]")

    if not additions_dir.exists():
        console.print("No tracker-additions directory found.")
        return {"added": 0, "updated": 0, "skipped": 0}

    tsv_files = sorted(
        [f for f in additions_dir.iterdir() if f.suffix == ".tsv"],
        key=lambda f: int(re.sub(r"\D", "", f.stem) or "0"),
    )

    if not tsv_files:
        console.print("[green]No pending additions to merge.[/green]")
        return {"added": 0, "updated": 0, "skipped": 0}

    console.print(f"[dim]Found {len(tsv_files)} pending additions[/dim]")

    added = updated = skipped = 0
    new_lines: list[str] = []

    for tsv_file in tsv_files:
        content = tsv_file.read_text(encoding="utf-8").strip()
        addition = parse_tsv_content(content, tsv_file.name)
        if not addition:
            skipped += 1
            continue

        report_num = None
        m = re.search(r"\[(\d+)\]", addition["report"])
        if m:
            report_num = int(m.group(1))

        duplicate = None
        if report_num:
            for app in existing:
                if app.report_num == report_num:
                    duplicate = app
                    break

        if not duplicate:
            for app in existing:
                if app.num == addition["num"]:
                    duplicate = app
                    break

        if not duplicate:
            norm_company = re.sub(r"[^a-z0-9]", "", addition["company"].lower())
            for app in existing:
                if app.normalized_company != norm_company:
                    continue
                if role_fuzzy_match(addition["role"], app.role):
                    duplicate = app
                    break

        if duplicate:
            new_score = float(re.search(r"([\d.]+)", re.sub(r"\*\*", "", addition["score"])).group(1)) if re.search(r"([\d.]+)", addition["score"]) else 0
            old_score = duplicate.parsed_score

            if new_score > old_score:
                console.print(
                    f"[blue]Update: #{duplicate.num} {addition['company']} — "
                    f"{addition['role']} ({old_score}→{new_score})[/blue]"
                )
                idx = None
                for i, line in enumerate(lines):
                    if line == duplicate.raw_line:
                        idx = i
                        break
                if idx is not None:
                    lines[idx] = (
                        f"| {duplicate.num} | {addition['date']} | {addition['company']} "
                        f"| {addition['role']} | {addition['score']} | {duplicate.status} "
                        f"| {duplicate.pdf} | {addition['report']} "
                        f"| Re-eval {addition['date']} ({old_score}→{new_score}). {addition['notes']} |"
                    )
                    updated += 1
            else:
                console.print(
                    f"[dim]Skip: {addition['company']} — {addition['role']} "
                    f"(existing #{duplicate.num} {old_score} >= new {new_score})[/dim]"
                )
                skipped += 1
        else:
            entry_num = addition["num"] if addition["num"] > max_num else max_num + 1
            if addition["num"] > max_num:
                max_num = addition["num"]
            else:
                max_num = entry_num

            new_line = (
                f"| {entry_num} | {addition['date']} | {addition['company']} "
                f"| {addition['role']} | {addition['score']} | {addition['status']} "
                f"| {addition['pdf']} | {addition['report']} | {addition['notes']} |"
            )
            new_lines.append(new_line)
            added += 1
            console.print(
                f"[green]Add #{entry_num}: {addition['company']} — "
                f"{addition['role']} ({addition['score']})[/green]"
            )

    if new_lines:
        insert_idx = -1
        for i, line in enumerate(lines):
            if "---" in line and line.startswith("|"):
                insert_idx = i + 1
                break
        if insert_idx >= 0:
            for j, new_line in enumerate(new_lines):
                lines.insert(insert_idx + j, new_line)

    if not dry_run:
        apps_file.write_text("\n".join(lines), encoding="utf-8")

        merged_dir.mkdir(parents=True, exist_ok=True)
        for tsv_file in tsv_files:
            shutil.move(str(tsv_file), str(merged_dir / tsv_file.name))
        console.print(f"\n[green]Moved {len(tsv_files)} TSVs to merged/[/green]")

    console.print(f"\n[bold]Summary: +{added} added, {updated} updated, {skipped} skipped[/bold]")
    if dry_run:
        console.print("[dim](dry-run — no changes written)[/dim]")

    return {"added": added, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Normalize: clean non-canonical statuses in applications.md
# ---------------------------------------------------------------------------


def normalize_statuses(paths: ProjectPaths, dry_run: bool = False) -> int:
    """
    Normalize all statuses in applications.md to canonical values.

    Returns the number of changes made.
    """
    apps_file = paths.applications
    if not apps_file.exists():
        console.print("No applications.md found. Nothing to normalize.")
        return 0

    content = apps_file.read_text(encoding="utf-8")
    lines = content.split("\n")
    changes = 0
    unknowns: list[dict] = []

    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        parts = [s.strip() for s in line.split("|")]
        if len(parts) < 9:
            continue
        if parts[1] in ("#", "---", ""):
            continue
        try:
            num = int(parts[1])
        except ValueError:
            continue

        raw_status = parts[6]
        canonical = ApplicationStatus.from_string(raw_status)

        if canonical.value == raw_status:
            continue

        old_status = raw_status
        parts[6] = canonical.value

        # Strip bold from score field
        if parts[5]:
            parts[5] = parts[5].replace("**", "")

        # Move DUPLICADO/repost info to notes
        cleaned_lower = re.sub(r"\*\*", "", raw_status).strip().lower()
        if re.match(r"^(duplicado|dup|repost)", cleaned_lower):
            existing_notes = parts[9] if len(parts) > 9 else ""
            if existing_notes and raw_status.strip() not in existing_notes:
                parts[9] = f"{raw_status.strip()}. {existing_notes}"
            elif not existing_notes:
                if len(parts) > 9:
                    parts[9] = raw_status.strip()
                else:
                    parts.append(raw_status.strip())

        lines[i] = "| " + " | ".join(parts[1:-1]) + " |"
        changes += 1
        console.print(f'#{num}: "{old_status}" → "{canonical.value}"')

    if unknowns:
        console.print(f"\n[yellow]{len(unknowns)} unknown statuses:[/yellow]")
        for u in unknowns:
            console.print(f"  #{u['num']} (line {u['line']}): \"{u['raw']}\"")

    console.print(f"\n[bold]{changes} statuses normalized[/bold]")

    if not dry_run and changes > 0:
        backup = Path(str(apps_file) + ".bak")
        shutil.copy2(apps_file, backup)
        apps_file.write_text("\n".join(lines), encoding="utf-8")
        console.print("[green]Written to applications.md (backup: applications.md.bak)[/green]")
    elif dry_run:
        console.print("[dim](dry-run — no changes written)[/dim]")
    else:
        console.print("[green]No changes needed[/green]")

    return changes


# ---------------------------------------------------------------------------
# Dedup: remove duplicate company+role entries
# ---------------------------------------------------------------------------


def dedup_tracker(paths: ProjectPaths, dry_run: bool = False) -> int:
    """
    Remove duplicate entries from applications.md.

    Groups by normalized company + fuzzy role match.
    Keeps entry with highest score; promotes status if a removed entry was further along.
    Returns number of duplicates removed.
    """
    apps_file = paths.applications
    if not apps_file.exists():
        console.print("No applications.md found. Nothing to dedup.")
        return 0

    content = apps_file.read_text(encoding="utf-8")
    lines = content.split("\n")

    entries: list[Application] = []
    entry_line_map: dict[int, int] = {}

    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        app = Application.from_pipe_line(line)
        if app and app.num > 0:
            entries.append(app)
            entry_line_map[app.num] = i

    console.print(f"[dim]{len(entries)} entries loaded[/dim]")

    groups: dict[str, list[Application]] = {}
    for entry in entries:
        key = entry.normalized_company
        groups.setdefault(key, []).append(entry)

    removed = 0
    lines_to_remove: set[int] = set()

    for company_key, company_entries in groups.items():
        if len(company_entries) < 2:
            continue

        processed: set[int] = set()
        for i in range(len(company_entries)):
            if i in processed:
                continue
            cluster = [company_entries[i]]
            processed.add(i)

            for j in range(i + 1, len(company_entries)):
                if j in processed:
                    continue
                if role_fuzzy_match(company_entries[i].role, company_entries[j].role):
                    cluster.append(company_entries[j])
                    processed.add(j)

            if len(cluster) < 2:
                continue

            cluster.sort(key=lambda a: a.parsed_score, reverse=True)
            keeper = cluster[0]

            best_rank = keeper.canonical_status.rank
            best_status = keeper.status
            for dup in cluster[1:]:
                dup_rank = dup.canonical_status.rank
                if dup_rank > best_rank:
                    best_rank = dup_rank
                    best_status = dup.status

            if best_status != keeper.status:
                line_idx = entry_line_map.get(keeper.num)
                if line_idx is not None:
                    parts = [s.strip() for s in lines[line_idx].split("|")]
                    parts[6] = best_status
                    lines[line_idx] = "| " + " | ".join(parts[1:-1]) + " |"
                    console.print(
                        f'  [blue]#{keeper.num}: status promoted to "{best_status}"[/blue]'
                    )

            for dup in cluster[1:]:
                line_idx = entry_line_map.get(dup.num)
                if line_idx is not None:
                    lines_to_remove.add(line_idx)
                    removed += 1
                    console.print(
                        f"[red]Remove #{dup.num} ({dup.company} — {dup.role}, {dup.score}) "
                        f"→ kept #{keeper.num} ({keeper.score})[/red]"
                    )

    for idx in sorted(lines_to_remove, reverse=True):
        lines.pop(idx)

    console.print(f"\n[bold]{removed} duplicates removed[/bold]")

    if not dry_run and removed > 0:
        backup = Path(str(apps_file) + ".bak")
        shutil.copy2(apps_file, backup)
        apps_file.write_text("\n".join(lines), encoding="utf-8")
        console.print("[green]Written to applications.md (backup: applications.md.bak)[/green]")
    elif dry_run:
        console.print("[dim](dry-run — no changes written)[/dim]")
    else:
        console.print("[green]No duplicates found[/green]")

    return removed
