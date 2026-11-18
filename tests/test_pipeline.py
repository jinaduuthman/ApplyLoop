"""Tests for the pipeline orchestrator."""

import pytest
from pathlib import Path

from applyloop.config import ProjectPaths
from applyloop.pipeline import _next_report_num, _add_tracker_entry
from applyloop.evaluator import EvaluationResult


class TestNextReportNum:
    def test_empty_project(self, tmp_path):
        paths = ProjectPaths(root=tmp_path)
        assert _next_report_num(paths) == 1

    def test_with_existing_reports(self, tmp_path):
        reports = tmp_path / "reports"
        reports.mkdir()
        (reports / "001-acme.md").write_text("report")
        (reports / "005-beta.md").write_text("report")
        paths = ProjectPaths(root=tmp_path)
        assert _next_report_num(paths) == 6

    def test_with_existing_tracker(self, tmp_path):
        (tmp_path / "reports").mkdir()
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 10 | 2024-01-15 | Acme | Eng | 3/5 | Applied | x | y | |\n"
        )
        paths = ProjectPaths(root=tmp_path)
        assert _next_report_num(paths) == 11


class TestAddTrackerEntry:
    def test_creates_file_if_missing(self, tmp_path):
        paths = ProjectPaths(root=tmp_path)
        (tmp_path / "data").mkdir(parents=True)
        (tmp_path / "reports").mkdir(parents=True)

        report_path = tmp_path / "reports" / "001-acme.md"
        report_path.write_text("report")

        result = EvaluationResult(
            company="Acme",
            role="Engineer",
            overall_score=3.8,
            date="2024-03-15",
            recommendation="apply",
        )
        _add_tracker_entry(paths, result, 1, report_path)

        apps = paths.applications
        assert apps.exists()
        content = apps.read_text()
        assert "Acme" in content
        assert "3.8/5" in content

    def test_appends_to_existing(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Existing | Dev | 3/5 | Applied | x | y | |\n"
        )
        (tmp_path / "reports").mkdir(parents=True)
        report_path = tmp_path / "reports" / "002-new.md"
        report_path.write_text("report")

        paths = ProjectPaths(root=tmp_path)
        result = EvaluationResult(
            company="NewCo",
            role="SRE",
            overall_score=4.2,
            date="2024-03-16",
            recommendation="apply",
        )
        _add_tracker_entry(paths, result, 2, report_path)

        content = apps.read_text()
        assert "Existing" in content
        assert "NewCo" in content
        assert "4.2/5" in content


class TestScannerImport:
    """Verify the scanner module imports cleanly (no Playwright at import time)."""

    def test_import(self):
        from applyloop.scanner import ScannedJob, _apply_title_filter

        jobs = [
            ScannedJob(url="u1", title="Senior Python Engineer", company="A"),
            ScannedJob(url="u2", title="Junior Designer", company="B"),
            ScannedJob(url="u3", title="ML Engineer", company="C"),
        ]
        filtered = _apply_title_filter(jobs, ["engineer"])
        assert len(filtered) == 2
        assert all("Engineer" in j.title for j in filtered)

    def test_no_filter_returns_all(self):
        from applyloop.scanner import ScannedJob, _apply_title_filter

        jobs = [
            ScannedJob(url="u1", title="Eng", company="A"),
            ScannedJob(url="u2", title="Des", company="B"),
        ]
        assert len(_apply_title_filter(jobs, [])) == 2
