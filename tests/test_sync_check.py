"""Tests for CV/profile sync check."""

import pytest
from pathlib import Path

from applyloop.config import ProjectPaths
from applyloop.sync_check import run_sync_check


class TestSyncCheck:
    def test_missing_cv_is_error(self, tmp_path):
        paths = ProjectPaths(root=tmp_path)
        errors, warnings = run_sync_check(paths)
        assert len(errors) > 0
        assert any("cv.md" in e for e in errors)

    def test_missing_profile_is_error(self, tmp_path):
        (tmp_path / "cv.md").write_text("A" * 200)
        paths = ProjectPaths(root=tmp_path)
        errors, warnings = run_sync_check(paths)
        assert any("profile.yml" in e for e in errors)

    def test_short_cv_is_warning(self, tmp_path):
        (tmp_path / "cv.md").write_text("short")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "profile.yml").write_text("full_name: Test\nemail: t@t.com\nlocation: X")
        paths = ProjectPaths(root=tmp_path)
        _, warnings = run_sync_check(paths)
        assert any("too short" in w for w in warnings)

    def test_example_data_in_profile_is_warning(self, tmp_path):
        (tmp_path / "cv.md").write_text("A" * 200)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "profile.yml").write_text(
            'full_name: "Jane Smith"\nemail: "jane@example.com"\nlocation: "SF"'
        )
        paths = ProjectPaths(root=tmp_path)
        _, warnings = run_sync_check(paths)
        assert any("example data" in w for w in warnings)

    def test_all_good(self, tmp_path):
        (tmp_path / "cv.md").write_text("A" * 200)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "profile.yml").write_text(
            'full_name: "Real Person"\nemail: "real@real.com"\nlocation: "NYC"'
        )
        paths = ProjectPaths(root=tmp_path)
        errors, warnings = run_sync_check(paths)
        assert len(errors) == 0
