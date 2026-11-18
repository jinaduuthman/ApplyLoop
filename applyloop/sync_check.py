"""CV and profile consistency validation."""

from __future__ import annotations

import re
import time
from pathlib import Path

from rich.console import Console

from .config import ProjectPaths

console = Console()

METRIC_PATTERN = re.compile(
    r"\b\d{2,4}\+?\s*(hours?|%|evals?|layers?|tests?|fields?|bases?)\b",
    re.IGNORECASE,
)


def run_sync_check(paths: ProjectPaths) -> tuple[list[str], list[str]]:
    """
    Validate that applyloop setup is internally consistent.

    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. cv.md exists and is substantial
    if not paths.cv.exists():
        errors.append(
            "cv.md not found in project root. Create it with your CV in markdown format."
        )
    else:
        cv_content = paths.cv.read_text(encoding="utf-8")
        if len(cv_content.strip()) < 100:
            warnings.append(
                "cv.md seems too short. Make sure it contains your full CV."
            )

    # 2. profile.yml exists and isn't example data
    if not paths.profile.exists():
        errors.append(
            "config/profile.yml not found. "
            "Run `applyloop init`, then fill in config/profile.yml."
        )
    else:
        profile_content = paths.profile.read_text(encoding="utf-8")
        required_fields = ["full_name", "email", "location"]
        for field in required_fields:
            if field not in profile_content or '"Jane Smith"' in profile_content:
                warnings.append(
                    f"config/profile.yml may still have example data. Check field: {field}"
                )
                break

    # 3. No hardcoded metrics in prompt files
    prompt_files = [
        (paths.modes_dir / "_shared.md", "_shared.md"),
        (paths.batch_dir / "batch-prompt.md", "batch-prompt.md"),
    ]

    for file_path, name in prompt_files:
        if not file_path.exists():
            continue
        content = file_path.read_text(encoding="utf-8")
        for i, line in enumerate(content.split("\n"), 1):
            if any(
                kw in line
                for kw in ("NEVER hardcode", "NUNCA hardcode")
            ):
                continue
            if line.startswith("#") or line.startswith("<!--"):
                continue
            matches = METRIC_PATTERN.findall(line)
            if matches:
                full_match = METRIC_PATTERN.search(line)
                if full_match:
                    warnings.append(
                        f'{name}:{i} — Possible hardcoded metric: "{full_match.group()}". '
                        "Should this be read from cv.md/article-digest.md?"
                    )

    # 4. article-digest.md freshness
    digest_path = paths.root / "article-digest.md"
    if digest_path.exists():
        mtime = digest_path.stat().st_mtime
        days_old = (time.time() - mtime) / (60 * 60 * 24)
        if days_old > 30:
            warnings.append(
                f"article-digest.md is {int(days_old)} days old. "
                "Consider updating if your projects have new metrics."
            )

    # Output
    console.print("\n[bold]=== applyloop sync check ===[/bold]\n")

    if not errors and not warnings:
        console.print("[green]All checks passed.[/green]")
    else:
        if errors:
            console.print(f"[red]ERRORS ({len(errors)}):[/red]")
            for e in errors:
                console.print(f"  [red]ERROR: {e}[/red]")
        if warnings:
            console.print(f"\n[yellow]WARNINGS ({len(warnings)}):[/yellow]")
            for w in warnings:
                console.print(f"  [yellow]WARN: {w}[/yellow]")

    console.print()
    return errors, warnings
