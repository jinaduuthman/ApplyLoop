"""Analytics engine — funnel metrics, response rates, score distributions."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from rich.console import Console
from rich.table import Table

from .config import ProjectPaths
from .models import Application, ApplicationStatus

console = Console()


@dataclass
class FunnelMetrics:
    total: int = 0
    evaluated: int = 0
    applied: int = 0
    responded: int = 0
    interview: int = 0
    offer: int = 0
    rejected: int = 0
    discarded: int = 0
    skipped: int = 0

    @property
    def conversion_rate(self) -> float:
        """Evaluated → Applied conversion rate."""
        return (self.applied / self.evaluated * 100) if self.evaluated > 0 else 0.0

    @property
    def response_rate(self) -> float:
        """Applied → Responded+ rate."""
        active = self.responded + self.interview + self.offer
        return (active / self.applied * 100) if self.applied > 0 else 0.0

    @property
    def interview_rate(self) -> float:
        """Applied → Interview+ rate."""
        interviews = self.interview + self.offer
        return (interviews / self.applied * 100) if self.applied > 0 else 0.0

    @property
    def offer_rate(self) -> float:
        """Applied → Offer rate."""
        return (self.offer / self.applied * 100) if self.applied > 0 else 0.0


@dataclass
class ScoreDistribution:
    bucket_0_1: int = 0
    bucket_1_2: int = 0
    bucket_2_3: int = 0
    bucket_3_4: int = 0
    bucket_4_5: int = 0
    avg_score: float = 0.0
    median_score: float = 0.0
    total_scored: int = 0


@dataclass
class TimeMetrics:
    avg_days_to_response: float = 0.0
    avg_days_in_pipeline: float = 0.0
    applications_per_week: float = 0.0
    busiest_day: str = ""
    total_days_active: int = 0


def _load_entries(paths: ProjectPaths) -> list[Application]:
    """Load all application entries from applications.md."""
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


def compute_funnel(entries: list[Application]) -> FunnelMetrics:
    """Compute pipeline funnel metrics."""
    metrics = FunnelMetrics(total=len(entries))

    for e in entries:
        status = e.canonical_status
        if status == ApplicationStatus.EVALUATED:
            metrics.evaluated += 1
        elif status == ApplicationStatus.APPLIED:
            metrics.applied += 1
        elif status == ApplicationStatus.RESPONDED:
            metrics.responded += 1
        elif status == ApplicationStatus.INTERVIEW:
            metrics.interview += 1
        elif status == ApplicationStatus.OFFER:
            metrics.offer += 1
        elif status == ApplicationStatus.REJECTED:
            metrics.rejected += 1
        elif status == ApplicationStatus.DISCARDED:
            metrics.discarded += 1
        elif status == ApplicationStatus.SKIP:
            metrics.skipped += 1

    return metrics


def compute_score_distribution(entries: list[Application]) -> ScoreDistribution:
    """Compute score distribution across all entries."""
    dist = ScoreDistribution()
    scores: list[float] = []

    for e in entries:
        score = e.parsed_score
        if score <= 0:
            continue
        scores.append(score)

        if score < 1:
            dist.bucket_0_1 += 1
        elif score < 2:
            dist.bucket_1_2 += 1
        elif score < 3:
            dist.bucket_2_3 += 1
        elif score < 4:
            dist.bucket_3_4 += 1
        else:
            dist.bucket_4_5 += 1

    if scores:
        dist.avg_score = sum(scores) / len(scores)
        sorted_scores = sorted(scores)
        mid = len(sorted_scores) // 2
        dist.median_score = (
            sorted_scores[mid]
            if len(sorted_scores) % 2
            else (sorted_scores[mid - 1] + sorted_scores[mid]) / 2
        )
        dist.total_scored = len(scores)

    return dist


def compute_time_metrics(entries: list[Application]) -> TimeMetrics:
    """Compute time-based metrics from application dates."""
    metrics = TimeMetrics()
    dates: list[date] = []
    day_counter: Counter[str] = Counter()

    for e in entries:
        d = e.parsed_date
        if d:
            dates.append(d)
            day_counter[d.strftime("%A")] += 1

    if not dates:
        return metrics

    dates.sort()
    earliest = dates[0]
    latest = dates[-1]
    total_days = (latest - earliest).days + 1
    metrics.total_days_active = total_days

    if total_days > 0:
        weeks = total_days / 7
        metrics.applications_per_week = len(dates) / weeks if weeks > 0 else 0

    if day_counter:
        metrics.busiest_day = day_counter.most_common(1)[0][0]

    return metrics


def compute_company_stats(entries: list[Application]) -> list[dict]:
    """Compute per-company statistics."""
    company_map: dict[str, list[Application]] = {}
    for e in entries:
        company_map.setdefault(e.company, []).append(e)

    stats = []
    for company, apps in sorted(company_map.items(), key=lambda x: -len(x[1])):
        scores = [a.parsed_score for a in apps if a.parsed_score > 0]
        stats.append({
            "company": company,
            "count": len(apps),
            "avg_score": sum(scores) / len(scores) if scores else 0,
            "statuses": [a.status for a in apps],
            "best_status": max(apps, key=lambda a: a.canonical_status.rank).status,
        })

    return stats


def run_analytics(paths: ProjectPaths) -> dict:
    """Run full analytics and display results. Returns metrics dict."""
    entries = _load_entries(paths)

    if not entries:
        console.print("[yellow]No application data found. Nothing to analyze.[/yellow]")
        return {}

    funnel = compute_funnel(entries)
    scores = compute_score_distribution(entries)
    time_m = compute_time_metrics(entries)
    company_stats = compute_company_stats(entries)

    # -- Funnel table
    console.print("\n[bold]Pipeline Funnel[/bold]")
    funnel_table = Table(show_header=True, header_style="bold")
    funnel_table.add_column("Stage", style="cyan")
    funnel_table.add_column("Count", justify="right")
    funnel_table.add_column("Bar", min_width=20)

    stages = [
        ("Total", funnel.total),
        ("Evaluated", funnel.evaluated),
        ("Applied", funnel.applied),
        ("Responded", funnel.responded),
        ("Interview", funnel.interview),
        ("Offer", funnel.offer),
        ("Rejected", funnel.rejected),
        ("Discarded", funnel.discarded),
        ("Skipped", funnel.skipped),
    ]

    max_count = max(c for _, c in stages) if stages else 1
    for label, count in stages:
        bar_len = int(count / max_count * 20) if max_count > 0 else 0
        bar = "█" * bar_len + "░" * (20 - bar_len)
        funnel_table.add_row(label, str(count), bar)

    console.print(funnel_table)

    # -- Conversion rates
    console.print("\n[bold]Conversion Rates[/bold]")
    rate_table = Table(show_header=True, header_style="bold")
    rate_table.add_column("Metric", style="cyan")
    rate_table.add_column("Rate", justify="right")

    rate_table.add_row("Eval → Applied", f"{funnel.conversion_rate:.1f}%")
    rate_table.add_row("Applied → Response", f"{funnel.response_rate:.1f}%")
    rate_table.add_row("Applied → Interview", f"{funnel.interview_rate:.1f}%")
    rate_table.add_row("Applied → Offer", f"{funnel.offer_rate:.1f}%")
    console.print(rate_table)

    # -- Score distribution
    console.print("\n[bold]Score Distribution[/bold]")
    score_table = Table(show_header=True, header_style="bold")
    score_table.add_column("Range", style="cyan")
    score_table.add_column("Count", justify="right")
    score_table.add_column("Bar", min_width=15)

    score_buckets = [
        ("0-1/5", scores.bucket_0_1),
        ("1-2/5", scores.bucket_1_2),
        ("2-3/5", scores.bucket_2_3),
        ("3-4/5", scores.bucket_3_4),
        ("4-5/5", scores.bucket_4_5),
    ]
    max_bucket = max(c for _, c in score_buckets) if score_buckets else 1
    for label, count in score_buckets:
        bar_len = int(count / max_bucket * 15) if max_bucket > 0 else 0
        bar = "█" * bar_len + "░" * (15 - bar_len)
        score_table.add_row(label, str(count), bar)

    console.print(score_table)
    console.print(
        f"[dim]Avg: {scores.avg_score:.2f}/5 | Median: {scores.median_score:.2f}/5 "
        f"| Scored: {scores.total_scored}[/dim]"
    )

    # -- Time metrics
    if time_m.total_days_active > 0:
        console.print("\n[bold]Time Metrics[/bold]")
        console.print(f"  Active for: {time_m.total_days_active} days")
        console.print(f"  Apps/week: {time_m.applications_per_week:.1f}")
        console.print(f"  Busiest day: {time_m.busiest_day}")

    # -- Top companies
    if company_stats:
        console.print("\n[bold]Top Companies (by applications)[/bold]")
        co_table = Table(show_header=True, header_style="bold")
        co_table.add_column("Company", style="cyan")
        co_table.add_column("Apps", justify="right")
        co_table.add_column("Avg Score", justify="right")
        co_table.add_column("Best Stage")

        for stat in company_stats[:10]:
            co_table.add_row(
                stat["company"],
                str(stat["count"]),
                f"{stat['avg_score']:.1f}/5" if stat["avg_score"] > 0 else "N/A",
                stat["best_status"],
            )
        console.print(co_table)

    return {
        "funnel": funnel,
        "scores": scores,
        "time": time_m,
        "company_stats": company_stats,
    }
