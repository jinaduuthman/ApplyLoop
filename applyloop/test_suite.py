"""Built-in test suite — comprehensive pre-merge validation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from rich.console import Console

from .config import ProjectPaths

console = Console()


def run_tests(paths: ProjectPaths, quick: bool = False) -> int:
    """
    Run comprehensive test suite similar to the original test-all.mjs.

    Returns exit code (0 = all pass).
    """
    passed = 0
    failed = 0
    warnings = 0

    def ok(msg: str) -> None:
        nonlocal passed
        console.print(f"  [green]✓[/green] {msg}")
        passed += 1

    def fail(msg: str) -> None:
        nonlocal failed
        console.print(f"  [red]✗[/red] {msg}")
        failed += 1

    def warn(msg: str) -> None:
        nonlocal warnings
        console.print(f"  [yellow]![/yellow] {msg}")
        warnings += 1

    console.print("\n[bold]applyloop test suite[/bold]\n")

    # -- 1. Module imports
    console.print("1. Module import checks")
    modules = [
        "applyloop.config",
        "applyloop.models",
        "applyloop.doctor",
        "applyloop.pdf",
        "applyloop.tracker",
        "applyloop.verify",
        "applyloop.sync_check",
        "applyloop.liveness",
        "applyloop.batch",
        "applyloop.analytics",
        "applyloop.export",
        "applyloop.reminders",
        "applyloop.keywords",
        "applyloop.dashboard.app",
        "applyloop.cli",
    ]

    for mod in modules:
        try:
            __import__(mod)
            ok(f"{mod} imports OK")
        except Exception as e:
            fail(f"{mod} import failed: {e}")

    # -- 2. Script syntax (compile check)
    console.print("\n2. Python syntax checks")
    py_files = list(Path(__file__).parent.rglob("*.py"))
    for f in py_files:
        try:
            compile(f.read_text(encoding="utf-8"), str(f), "exec")
            ok(f"{f.name} syntax OK")
        except SyntaxError as e:
            fail(f"{f.name} syntax error: {e}")

    # -- 3. Data contract
    console.print("\n3. Data contract validation")

    system_files = {
        "templates/states.yml": paths.states,
        "templates/cv-template.html": paths.cv_template,
        "profile.example.yml": paths.profile_example,
    }

    for f, p in system_files.items():
        if p.exists():
            ok(f"System file exists: {f}")
        else:
            warn(f"System file missing: {f} (may not be needed yet)")

    # -- 4. Config parsing
    console.print("\n4. Configuration parsing")

    try:
        from .config import load_states
        states = load_states(paths)
        if states:
            ok(f"states.yml loaded: {len(states)} states")
        else:
            warn("states.yml empty or missing")
    except Exception as e:
        fail(f"states.yml parse error: {e}")

    try:
        from .config import load_profile
        profile = load_profile(paths)
        if profile:
            ok(f"profile.yml loaded: {profile.full_name}")
        else:
            warn("profile.yml not found (expected for fresh setup)")
    except Exception as e:
        fail(f"profile.yml parse error: {e}")

    # -- 5. Model tests
    console.print("\n5. Model unit tests")

    from .models import ApplicationStatus, Application, role_fuzzy_match

    try:
        assert ApplicationStatus.from_string("Applied") == ApplicationStatus.APPLIED
        assert ApplicationStatus.from_string("aplicado") == ApplicationStatus.APPLIED
        assert ApplicationStatus.from_string("enviada") == ApplicationStatus.APPLIED
        assert ApplicationStatus.from_string("SKIP") == ApplicationStatus.SKIP
        assert ApplicationStatus.from_string("no aplicar") == ApplicationStatus.SKIP
        assert ApplicationStatus.from_string("duplicado #123") == ApplicationStatus.DISCARDED
        ok("ApplicationStatus.from_string aliases work")
    except AssertionError as e:
        fail(f"ApplicationStatus alias test failed: {e}")

    try:
        assert ApplicationStatus.OFFER.rank > ApplicationStatus.INTERVIEW.rank
        assert ApplicationStatus.INTERVIEW.rank > ApplicationStatus.APPLIED.rank
        assert ApplicationStatus.APPLIED.rank > ApplicationStatus.EVALUATED.rank
        ok("ApplicationStatus ranking is correct")
    except AssertionError as e:
        fail(f"Status ranking test failed: {e}")

    try:
        assert role_fuzzy_match("Senior Software Engineer", "Senior Software Eng")
        assert role_fuzzy_match("ML Engineer - Platform", "Machine Learning Engineer Platform")
        assert not role_fuzzy_match("CEO", "Junior Intern")
        ok("role_fuzzy_match works")
    except AssertionError as e:
        fail(f"role_fuzzy_match test failed: {e}")

    try:
        line = "| 1 | 2024-01-15 | Acme | Senior Dev | 3.5/5 | Applied | cv.pdf | [1](reports/001.md) | Good fit |"
        app = Application.from_pipe_line(line)
        assert app is not None
        assert app.num == 1
        assert app.company == "Acme"
        assert app.parsed_score == 3.5
        assert app.report_num == 1
        assert app.canonical_status == ApplicationStatus.APPLIED
        ok("Application.from_pipe_line works")
    except (AssertionError, Exception) as e:
        fail(f"Application parsing test failed: {e}")

    # -- 6. ATS normalization
    console.print("\n6. ATS normalization")

    try:
        from .pdf import normalize_text_for_ats
        html = '<p>Hello\u2014World \u201Cquote\u201D</p><style>.x { content: "\u2014"; }</style>'
        result, replacements = normalize_text_for_ats(html)
        assert "\u2014" not in result.split("</style>")[0].split("<style>")[0]
        assert "em-dash" in replacements
        ok("ATS text normalization works")
    except (AssertionError, Exception) as e:
        fail(f"ATS normalization test failed: {e}")

    # -- 7. Keyword analysis
    console.print("\n7. Keyword analysis")

    try:
        from .keywords import analyze_keywords
        result = analyze_keywords(
            "We need a Python developer with experience in Django and PostgreSQL",
            "I am a Python developer skilled in Flask, FastAPI, and MySQL",
        )
        assert "python" in result["tech_match"]
        assert result["match_percentage"] > 0
        ok("Keyword analysis works")
    except (AssertionError, Exception) as e:
        fail(f"Keyword analysis test failed: {e}")

    # -- 8. Pytest (if available and not quick)
    if not quick:
        console.print("\n8. Pytest suite")
        tests_dir = paths.root / "tests"
        if tests_dir.exists() and any(tests_dir.glob("test_*.py")):
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(tests_dir), "-v", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                ok("Pytest suite passed")
            else:
                fail("Pytest suite failed")
                console.print(f"[dim]{result.stdout[-500:]}[/dim]")
        else:
            warn("No pytest tests found")
    else:
        console.print("\n8. Pytest suite (skipped --quick)")

    # -- Summary
    console.print("\n" + "=" * 50)
    console.print(f"[bold]Results: {passed} passed, {failed} failed, {warnings} warnings[/bold]")

    if failed > 0:
        console.print("[red]TESTS FAILED[/red]\n")
        return 1
    elif warnings > 0:
        console.print("[yellow]Tests passed with warnings[/yellow]\n")
        return 0
    else:
        console.print("[green]All tests passed[/green]\n")
        return 0
