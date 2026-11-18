"""Interactive TUI dashboard using Textual."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, Container
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    Label,
    TabbedContent,
    TabPane,
)
from textual.screen import Screen

from applyloop.config import ProjectPaths
from applyloop.models import Application, ApplicationStatus


class StatsPanel(Static):
    """Summary statistics panel."""

    def __init__(self, entries: list[Application], **kwargs):
        super().__init__(**kwargs)
        self.entries = entries

    def compose(self) -> ComposeResult:
        total = len(self.entries)
        applied = sum(1 for e in self.entries if e.canonical_status == ApplicationStatus.APPLIED)
        interview = sum(1 for e in self.entries if e.canonical_status == ApplicationStatus.INTERVIEW)
        offer = sum(1 for e in self.entries if e.canonical_status == ApplicationStatus.OFFER)
        rejected = sum(1 for e in self.entries if e.canonical_status == ApplicationStatus.REJECTED)
        scores = [e.parsed_score for e in self.entries if e.parsed_score > 0]
        avg_score = sum(scores) / len(scores) if scores else 0

        yield Label(f"[bold]Pipeline Overview[/bold]", id="stats-title")
        yield Label(
            f"Total: [cyan]{total}[/cyan]  "
            f"Applied: [blue]{applied}[/blue]  "
            f"Interview: [yellow]{interview}[/yellow]  "
            f"Offer: [green]{offer}[/green]  "
            f"Rejected: [red]{rejected}[/red]  "
            f"Avg Score: [magenta]{avg_score:.1f}/5[/magenta]"
        )


class PipelineTable(DataTable):
    """Main applications data table."""

    STATUS_STYLES = {
        "Evaluated": "yellow",
        "Applied": "blue",
        "Responded": "cyan",
        "Interview": "magenta",
        "Offer": "green",
        "Rejected": "red",
        "Discarded": "dim",
        "SKIP": "dim italic",
    }

    def populate(self, entries: list[Application], status_filter: str | None = None) -> None:
        self.clear(columns=True)
        self.add_columns("#", "Date", "Company", "Role", "Score", "Status", "Notes")

        filtered = entries
        if status_filter:
            filtered = [
                e for e in entries
                if e.canonical_status.value.lower() == status_filter.lower()
            ]

        for e in filtered:
            style = self.STATUS_STYLES.get(e.canonical_status.value, "")
            status_display = f"[{style}]{e.status}[/{style}]" if style else e.status
            self.add_row(
                str(e.num),
                e.date,
                e.company,
                e.role,
                e.score,
                status_display,
                e.notes[:40] if e.notes else "",
            )


class FunnelPanel(Static):
    """Visual funnel display."""

    def __init__(self, entries: list[Application], **kwargs):
        super().__init__(**kwargs)
        self.entries = entries

    def compose(self) -> ComposeResult:
        from collections import Counter
        status_counts = Counter(e.canonical_status.value for e in self.entries)

        stages = [
            ("Evaluated", "yellow"),
            ("Applied", "blue"),
            ("Responded", "cyan"),
            ("Interview", "magenta"),
            ("Offer", "green"),
            ("Rejected", "red"),
            ("Discarded", "dim"),
            ("SKIP", "dim"),
        ]

        max_count = max(status_counts.values()) if status_counts else 1
        yield Label("[bold]Application Funnel[/bold]")
        for stage, color in stages:
            count = status_counts.get(stage, 0)
            bar_len = int(count / max_count * 30) if max_count > 0 else 0
            bar = "█" * bar_len
            yield Label(f"[{color}]{stage:>12}[/{color}] [{color}]{bar}[/{color}] {count}")


class DashboardApp(App):
    """Career-ops interactive dashboard."""

    TITLE = "applyloop dashboard"
    CSS = """
    Screen {
        layout: vertical;
    }
    #stats-panel {
        height: 3;
        padding: 0 1;
        background: $surface;
    }
    #stats-title {
        text-style: bold;
    }
    PipelineTable {
        height: 1fr;
    }
    FunnelPanel {
        height: auto;
        max-height: 12;
        padding: 1;
    }
    TabbedContent {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "filter_all", "All"),
        Binding("e", "filter_evaluated", "Evaluated"),
        Binding("p", "filter_applied", "Applied"),
        Binding("i", "filter_interview", "Interview"),
        Binding("o", "filter_offer", "Offer"),
    ]

    def __init__(self, paths: ProjectPaths | None = None, **kwargs):
        super().__init__(**kwargs)
        self.paths = paths or ProjectPaths()
        self.entries: list[Application] = []
        self.current_filter: str | None = None

    def _load_entries(self) -> list[Application]:
        apps_file = self.paths.applications
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

    def compose(self) -> ComposeResult:
        yield Header()

        self.entries = self._load_entries()
        yield StatsPanel(self.entries, id="stats-panel")

        with TabbedContent():
            with TabPane("Pipeline", id="tab-pipeline"):
                table = PipelineTable(id="main-table")
                yield table

            with TabPane("Funnel", id="tab-funnel"):
                yield FunnelPanel(self.entries)

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#main-table", PipelineTable)
        table.populate(self.entries)

    def action_refresh(self) -> None:
        self.entries = self._load_entries()
        table = self.query_one("#main-table", PipelineTable)
        table.populate(self.entries, self.current_filter)
        self.notify("Data refreshed")

    def _apply_filter(self, status: str | None) -> None:
        self.current_filter = status
        table = self.query_one("#main-table", PipelineTable)
        table.populate(self.entries, status)
        label = status or "All"
        self.notify(f"Filter: {label}")

    def action_filter_all(self) -> None:
        self._apply_filter(None)

    def action_filter_evaluated(self) -> None:
        self._apply_filter("Evaluated")

    def action_filter_applied(self) -> None:
        self._apply_filter("Applied")

    def action_filter_interview(self) -> None:
        self._apply_filter("Interview")

    def action_filter_offer(self) -> None:
        self._apply_filter("Offer")


def run_dashboard(paths: ProjectPaths | None = None) -> None:
    """Launch the interactive TUI dashboard."""
    app = DashboardApp(paths=paths)
    app.run()
