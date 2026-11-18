"""Tests for tracker operations (merge, normalize, dedup)."""

import pytest
from pathlib import Path

from applyloop.config import ProjectPaths
from applyloop.tracker import (
    parse_tsv_content,
    merge_tracker,
    normalize_statuses,
    dedup_tracker,
)


class TestParseTsvContent:
    def test_tab_separated_9_cols(self):
        content = "1\t2024-01-15\tAcme\tEngineer\tApplied\t3.5/5\tcv.pdf\t[1](r.md)\tnotes"
        result = parse_tsv_content(content, "test.tsv")
        assert result is not None
        assert result["num"] == 1
        assert result["company"] == "Acme"
        assert result["role"] == "Engineer"
        assert result["notes"] == "notes"

    def test_tab_separated_8_cols(self):
        content = "2\t2024-01-16\tBeta\tDev\tApplied\t4.0/5\tcv.pdf\t[2](r.md)"
        result = parse_tsv_content(content, "test.tsv")
        assert result is not None
        assert result["num"] == 2
        assert result["notes"] == ""

    def test_pipe_delimited(self):
        content = "| 3 | 2024-01-17 | Gamma | Designer | 3.0/5 | Evaluated | cv.pdf | [3](r.md) |"
        result = parse_tsv_content(content, "test.tsv")
        assert result is not None
        assert result["num"] == 3
        assert result["company"] == "Gamma"

    def test_empty_content(self):
        assert parse_tsv_content("", "test.tsv") is None

    def test_too_few_columns(self):
        assert parse_tsv_content("1\t2024\tAcme", "test.tsv") is None

    def test_invalid_num(self):
        content = "abc\t2024-01-15\tAcme\tEng\tApplied\t3/5\tx\ty"
        assert parse_tsv_content(content, "test.tsv") is None

    def test_swapped_score_status_columns(self):
        content = "1\t2024-01-15\tAcme\tEng\t3.5/5\tApplied\tcv.pdf\t[1](r.md)"
        result = parse_tsv_content(content, "test.tsv")
        assert result is not None
        assert result["status"] == "Applied"

    def test_status_normalization(self):
        content = "1\t2024-01-15\tAcme\tEng\tenviad\t3.5/5\tcv.pdf\t[1](r.md)"
        result = parse_tsv_content(content, "test.tsv")
        assert result is not None


class TestMergeTracker:
    def test_no_apps_file(self, tmp_path):
        paths = ProjectPaths(root=tmp_path)
        result = merge_tracker(paths)
        assert result["added"] == 0

    def test_no_additions_dir(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text("| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
        paths = ProjectPaths(root=tmp_path)
        result = merge_tracker(paths)
        assert result["added"] == 0

    def test_no_pending_tsvs(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text("| # | Date | Company |\n| --- | --- | --- |\n")
        additions = tmp_path / "batch" / "tracker-additions"
        additions.mkdir(parents=True)
        paths = ProjectPaths(root=tmp_path)
        result = merge_tracker(paths)
        assert result["added"] == 0

    def test_add_new_entry(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        )
        additions = tmp_path / "batch" / "tracker-additions"
        additions.mkdir(parents=True)
        (additions / "001.tsv").write_text(
            "1\t2024-01-15\tAcme\tEngineer\tEvaluated\t3.5/5\tcv.pdf\t[1](reports/001.md)\tnotes"
        )
        (tmp_path / "batch" / "tracker-additions" / "merged").mkdir(parents=True, exist_ok=True)

        paths = ProjectPaths(root=tmp_path)
        result = merge_tracker(paths)
        assert result["added"] == 1

        content = apps.read_text()
        assert "Acme" in content

    def test_skip_duplicate(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Engineer | 4.0/5 | Applied | cv.pdf | [1](r.md) | first |"
        )
        additions = tmp_path / "batch" / "tracker-additions"
        additions.mkdir(parents=True)
        (additions / "001.tsv").write_text(
            "1\t2024-01-16\tAcme\tEngineer\tEvaluated\t3.0/5\tcv.pdf\t[1](reports/001.md)\tredo"
        )

        paths = ProjectPaths(root=tmp_path)
        result = merge_tracker(paths, dry_run=True)
        assert result["skipped"] == 1


class TestNormalizeStatuses:
    def test_no_apps_file(self, tmp_path):
        paths = ProjectPaths(root=tmp_path)
        assert normalize_statuses(paths) == 0

    def test_normalize_bold_status(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Eng | 3/5 | **Applied** | x | y | |\n"
        )
        paths = ProjectPaths(root=tmp_path)
        changes = normalize_statuses(paths)
        assert changes == 1

        content = apps.read_text()
        assert "**" not in content

    def test_dry_run_no_writes(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        original = (
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Eng | 3/5 | **Applied** | x | y | |\n"
        )
        apps.write_text(original)
        paths = ProjectPaths(root=tmp_path)
        normalize_statuses(paths, dry_run=True)
        assert apps.read_text() == original


class TestDedupTracker:
    def test_no_apps_file(self, tmp_path):
        paths = ProjectPaths(root=tmp_path)
        assert dedup_tracker(paths) == 0

    def test_no_duplicates(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Engineer | 3/5 | Applied | x | y | |\n"
            "| 2 | 2024-01-16 | Beta | Designer | 4/5 | Applied | x | y | |\n"
        )
        paths = ProjectPaths(root=tmp_path)
        assert dedup_tracker(paths) == 0

    def test_removes_duplicate_keeps_higher_score(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Senior Engineer | 4.0/5 | Applied | x | y | |\n"
            "| 2 | 2024-01-16 | Acme | Senior Software Engineer | 3.0/5 | Evaluated | x | y | |\n"
        )
        paths = ProjectPaths(root=tmp_path)
        removed = dedup_tracker(paths)
        assert removed == 1

        content = apps.read_text()
        assert "4.0/5" in content
        assert "3.0/5" not in content

    def test_promotes_status_from_removed_entry(self, tmp_path):
        apps = tmp_path / "data" / "applications.md"
        apps.parent.mkdir(parents=True)
        apps.write_text(
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 2024-01-15 | Acme | Senior Engineer | 4.0/5 | Evaluated | x | y | |\n"
            "| 2 | 2024-01-16 | Acme | Senior Software Engineer | 3.0/5 | Interview | x | y | |\n"
        )
        paths = ProjectPaths(root=tmp_path)
        dedup_tracker(paths)
        content = apps.read_text()
        assert "Interview" in content
