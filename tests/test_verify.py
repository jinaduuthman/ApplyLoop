"""Tests for pipeline verification."""

import pytest
from pathlib import Path

from applyloop.config import ProjectPaths
from applyloop.verify import verify_pipeline


class TestVerifyPipeline:
    def test_no_apps_file_returns_zero(self, tmp_path):
        paths = ProjectPaths(root=tmp_path)
        errors, warnings = verify_pipeline(paths)
        assert errors == 0
        assert warnings == 0

    def test_clean_pipeline(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Eng | 3.5/5 | Applied | x | y | |\n"
            "| 2 | 2024-01-16 | Beta | Dev | 4.0/5 | Evaluated | x | y | |\n"
        )
        paths = ProjectPaths(root=tmp_path)
        errors, warnings = verify_pipeline(paths)
        assert errors == 0

    def test_detects_non_canonical_status(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Eng | 3.5/5 | FooBarStatus | x | y | |\n"
        )
        paths = ProjectPaths(root=tmp_path)
        errors, _ = verify_pipeline(paths)
        assert errors > 0

    def test_detects_bold_in_status(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Eng | 3.5/5 | **Applied** | x | y | |\n"
        )
        paths = ProjectPaths(root=tmp_path)
        errors, _ = verify_pipeline(paths)
        assert errors > 0

    def test_detects_invalid_score(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Eng | abc | Applied | x | y | |\n"
        )
        paths = ProjectPaths(root=tmp_path)
        errors, _ = verify_pipeline(paths)
        assert errors > 0

    def test_accepts_na_score(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Eng | N/A | Applied | x | y | |\n"
        )
        paths = ProjectPaths(root=tmp_path)
        errors, _ = verify_pipeline(paths)
        assert errors == 0

    def test_detects_pending_tsvs(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        )
        additions = tmp_path / "batch" / "tracker-additions"
        additions.mkdir(parents=True)
        (additions / "001.tsv").write_text("pending")

        paths = ProjectPaths(root=tmp_path)
        _, warnings = verify_pipeline(paths)
        assert warnings > 0
