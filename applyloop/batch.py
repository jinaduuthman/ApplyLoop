"""Batch orchestration runner — parallel job evaluations."""

from __future__ import annotations

import csv
import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from .config import ProjectPaths
from .tracker import merge_tracker

console = Console()


@dataclass
class BatchJob:
    job_id: str
    url: str
    jd_file: str = ""
    report_num: int = 0
    status: str = "pending"  # pending, running, completed, failed
    start_time: float = 0.0
    end_time: float = 0.0
    error: str = ""


@dataclass
class BatchConfig:
    input_file: Path
    prompt_file: Path
    max_parallel: int = 3
    max_retries: int = 2
    timeout: int = 300
    ai_command: str = "claude"


def load_batch_input(input_file: Path) -> list[BatchJob]:
    """Load batch input from a TSV file (url, optional jd_file)."""
    jobs: list[BatchJob] = []

    if not input_file.exists():
        console.print(f"[red]Input file not found: {input_file}[/red]")
        return jobs

    content = input_file.read_text(encoding="utf-8")
    for i, line in enumerate(content.strip().split("\n"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        url = parts[0].strip()
        jd_file = parts[1].strip() if len(parts) > 1 else ""
        jobs.append(BatchJob(
            job_id=f"batch-{i:03d}",
            url=url,
            jd_file=jd_file,
            report_num=i,
        ))

    return jobs


def _build_prompt(
    template: str,
    job: BatchJob,
    date_str: str,
) -> str:
    """Fill placeholders in the batch prompt template."""
    prompt = template
    prompt = prompt.replace("{{URL}}", job.url)
    prompt = prompt.replace("{{JD_FILE}}", job.jd_file)
    prompt = prompt.replace("{{REPORT_NUM}}", str(job.report_num))
    prompt = prompt.replace("{{DATE}}", date_str)
    prompt = prompt.replace("{{ID}}", job.job_id)
    return prompt


def _run_single_job(
    job: BatchJob,
    config: BatchConfig,
    prompt_template: str,
    date_str: str,
    results: list[BatchJob],
    lock: threading.Lock,
) -> None:
    """Execute a single batch job using the configured AI command."""
    job.status = "running"
    job.start_time = time.time()

    prompt = _build_prompt(prompt_template, job, date_str)

    for attempt in range(config.max_retries + 1):
        try:
            result = subprocess.run(
                [config.ai_command, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=config.timeout,
                cwd=str(config.input_file.parent.parent),
            )

            if result.returncode == 0:
                job.status = "completed"
                job.end_time = time.time()
                with lock:
                    results.append(job)
                return
            else:
                job.error = result.stderr[:200]
                if attempt < config.max_retries:
                    time.sleep(5 * (attempt + 1))

        except subprocess.TimeoutExpired:
            job.error = f"Timeout after {config.timeout}s"
            if attempt < config.max_retries:
                time.sleep(5)
        except FileNotFoundError:
            job.error = f"Command not found: {config.ai_command}"
            break
        except Exception as exc:
            job.error = str(exc)[:200]
            break

    job.status = "failed"
    job.end_time = time.time()
    with lock:
        results.append(job)


def run_batch(
    paths: ProjectPaths,
    input_file: Path | None = None,
    max_parallel: int = 3,
    ai_command: str = "claude",
    dry_run: bool = False,
) -> dict:
    """
    Run batch job evaluations in parallel.

    Returns summary dict with completed/failed counts.
    """
    batch_dir = paths.batch_dir
    inp = input_file or (batch_dir / "batch-input.tsv")
    prompt_file = batch_dir / "batch-prompt.md"

    if not prompt_file.exists():
        console.print("[red]batch/batch-prompt.md not found[/red]")
        return {"completed": 0, "failed": 0, "total": 0}

    jobs = load_batch_input(inp)
    if not jobs:
        console.print("[yellow]No jobs to process[/yellow]")
        return {"completed": 0, "failed": 0, "total": 0}

    prompt_template = prompt_file.read_text(encoding="utf-8")
    date_str = datetime.now().strftime("%Y-%m-%d")

    console.print(f"[bold]Batch runner: {len(jobs)} jobs, {max_parallel} parallel[/bold]")

    if dry_run:
        for job in jobs:
            console.print(f"  [dim]{job.job_id}: {job.url}[/dim]")
        console.print("[dim](dry-run — no jobs executed)[/dim]")
        return {"completed": 0, "failed": 0, "total": len(jobs)}

    config = BatchConfig(
        input_file=inp,
        prompt_file=prompt_file,
        max_parallel=max_parallel,
        ai_command=ai_command,
    )

    results: list[BatchJob] = []
    lock = threading.Lock()
    semaphore = threading.Semaphore(max_parallel)
    threads: list[threading.Thread] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Processing...", total=len(jobs))

        def run_with_semaphore(job: BatchJob) -> None:
            with semaphore:
                _run_single_job(job, config, prompt_template, date_str, results, lock)
                progress.advance(task)

        for job in jobs:
            t = threading.Thread(target=run_with_semaphore, args=(job,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    completed = sum(1 for r in results if r.status == "completed")
    failed = sum(1 for r in results if r.status == "failed")

    console.print(f"\n[bold]Batch complete: {completed} succeeded, {failed} failed[/bold]")

    for r in results:
        if r.status == "failed":
            console.print(f"  [red]{r.job_id}: {r.error}[/red]")

    if completed > 0:
        console.print("\n[dim]Running tracker merge...[/dim]")
        merge_tracker(paths)

    return {"completed": completed, "failed": failed, "total": len(jobs)}
