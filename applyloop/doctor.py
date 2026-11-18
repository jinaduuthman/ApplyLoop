"""Environment validation — checks all prerequisites for applyloop."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from .config import ProjectPaths

console = Console()


@dataclass
class CheckResult:
    passed: bool
    label: str
    fixes: list[str] | None = None


def check_python_version() -> CheckResult:
    v = sys.version_info
    if v >= (3, 11):
        return CheckResult(True, f"Python >= 3.11 (v{v.major}.{v.minor}.{v.micro})")
    return CheckResult(
        False,
        f"Python >= 3.11 required (found v{v.major}.{v.minor}.{v.micro})",
        ["Install Python 3.11+ from https://python.org"],
    )


def check_dependencies() -> CheckResult:
    missing = []
    for pkg in ["typer", "rich", "yaml", "playwright", "textual", "openpyxl"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if not missing:
        return CheckResult(True, "All Python dependencies installed")
    return CheckResult(
        False,
        f"Missing packages: {', '.join(missing)}",
        ["Run: pip install -e '.[dev]'"],
    )


def check_playwright_browser() -> CheckResult:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = p.chromium.executable_path
            if Path(path).exists():
                return CheckResult(True, "Playwright Chromium installed")
    except Exception:
        pass
    return CheckResult(
        False,
        "Playwright Chromium not installed",
        ["Run: playwright install chromium"],
    )


def check_file_exists(path: Path, label: str, fixes: list[str]) -> CheckResult:
    if path.exists():
        return CheckResult(True, f"{label} found")
    return CheckResult(False, f"{label} not found", fixes)


def check_fonts(paths: ProjectPaths) -> CheckResult:
    fonts_dir = paths.fonts_dir
    if not fonts_dir.exists():
        return CheckResult(
            False,
            "fonts/ directory not found",
            ["Create fonts/ directory with font files for PDF generation"],
        )
    files = list(fonts_dir.iterdir())
    if not files:
        return CheckResult(
            False,
            "fonts/ directory is empty",
            ["Add font files (e.g. .woff2, .ttf) to fonts/"],
        )
    return CheckResult(True, "Fonts directory ready")


def check_auto_dir(path: Path, name: str) -> CheckResult:
    if path.exists():
        return CheckResult(True, f"{name}/ directory ready")
    try:
        path.mkdir(parents=True, exist_ok=True)
        return CheckResult(True, f"{name}/ directory ready (auto-created)")
    except OSError:
        return CheckResult(
            False,
            f"{name}/ directory could not be created",
            [f"Run: mkdir -p {name}"],
        )


def check_agent(paths: ProjectPaths) -> list[CheckResult]:
    """Checks for the job-search agent (applyloop hunt / submit)."""
    import importlib.util
    import shutil

    from .latex_cv import tex_engine
    from .llm import resolve_config
    from .settings import load_settings

    out: list[CheckResult] = []
    cfg = paths.config_dir / "applyloop.yml"
    out.append(CheckResult(cfg.exists(), "config/applyloop.yml",
                           ["applyloop init   (creates config/applyloop.yml), then edit tracks"]))
    s = load_settings(paths)
    if not s.tracks:
        out.append(CheckResult(False, "CV tracks configured", ["Add at least one track to applyloop.yml"]))
    for t in s.tracks:
        ok = bool(t.cv and t.cv.exists()) or bool(t.fallback_pdf and t.fallback_pdf.exists())
        out.append(CheckResult(ok, f"track {t.id}: {t.cv.name if t.cv else '—'}",
                               [f"File not found: {t.cv}"]))
    eng = tex_engine()
    out.append(CheckResult(bool(eng), f"TeX engine ({eng[0] if eng else 'none'})",
                           ["brew install tectonic   (lightest; fetches packages on demand)",
                            "or: brew install --cask mactex-no-gui",
                            "Without TeX, tracks' fallback_pdf files are used untailored"]))
    out.append(CheckResult((paths.config_dir / "answers.yml").exists(), "config/answers.yml",
                           ["applyloop init   (creates config/answers.yml), then review every answer"]))
    from .llm import TASKS
    seen = set()
    for task in ("",) + TASKS:
        c = resolve_config(s.llm, task)
        key = (c.provider, c.model)
        if key in seen:
            continue
        seen.add(key)
        label = f"AI {task or 'default'}: {c.provider or 'none'}:{c.model or '—'}"
        fixes = []
        if not c.provider:
            fixes = ["Optional: set OPENAI_API_KEY (or ANTHROPIC/GEMINI) in .env and llm.provider/model",
                     "Without AI: keyword scoring + deterministic CV tailoring only"]
        elif not c.model:
            fixes = [f"Set llm.model in config/applyloop.yml (`applyloop llm models` lists IDs)"]
        elif not c.configured:
            fixes = [f"Add the {c.provider.upper()} API key to .env"]
        out.append(CheckResult(c.configured, label, fixes))
    if s.browser.get("mode") == "profile" and s.browser.get("channel") == "chrome":
        chrome = shutil.which("google-chrome") or Path("/Applications/Google Chrome.app").exists()
        out.append(CheckResult(bool(chrome), "Google Chrome installed (browser.channel=chrome)",
                               ["Install Chrome, or set browser.channel: '' to use bundled Chromium"]))
    if s.sheets.get("enabled"):
        out.append(CheckResult(importlib.util.find_spec("gspread") is not None, "gspread installed",
                               ["pip install 'applyloop[sheets]'"]))
        key = Path(s.sheets.get("service_account_json", "")).expanduser()
        out.append(CheckResult(key.exists(), "Google service-account key", [f"Missing {key}"]))
    return out


def run_doctor(paths: ProjectPaths | None = None) -> int:
    """Run all health checks. Returns exit code (0 = all pass)."""
    if paths is None:
        paths = ProjectPaths()

    console.print("\n[bold]applyloop doctor[/bold]")
    console.print("=" * 30, "\n")

    checks = [
        check_python_version(),
        check_dependencies(),
        check_playwright_browser(),
        check_file_exists(
            paths.profile,
            "config/profile.yml",
            ["Run: applyloop init",
             "Then edit it with your details"],
        ),
        check_fonts(paths),
        *check_agent(paths),
        check_auto_dir(paths.data_dir, "data"),
        check_auto_dir(paths.output_dir, "output"),
        check_auto_dir(paths.reports_dir, "reports"),
    ]

    failures = 0
    for result in checks:
        if result.passed:
            console.print(f"[green]✓[/green] {result.label}")
        else:
            failures += 1
            console.print(f"[red]✗[/red] {result.label}")
            if result.fixes:
                for fix in result.fixes:
                    console.print(f"  [dim]→ {fix}[/dim]")

    console.print()
    if failures > 0:
        console.print(
            f"Result: {failures} issue{'s' if failures != 1 else ''} found. "
            "Fix them and run [bold]applyloop doctor[/bold] again."
        )
        return 1

    console.print(
        "Result: All checks passed. You're ready to go!"
    )
    return 0
