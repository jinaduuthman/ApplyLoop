"""Tests for environment validation (doctor)."""

import pytest
from pathlib import Path

from applyloop.doctor import (
    check_python_version,
    check_file_exists,
    check_auto_dir,
    check_fonts,
    CheckResult,
)
from applyloop.config import ProjectPaths


class TestCheckPythonVersion:
    def test_passes_on_current_python(self):
        result = check_python_version()
        assert result.passed is True
        assert "Python" in result.label


class TestCheckFileExists:
    def test_file_exists(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = check_file_exists(f, "test.txt", ["fix it"])
        assert result.passed is True

    def test_file_missing(self, tmp_path):
        f = tmp_path / "missing.txt"
        result = check_file_exists(f, "missing.txt", ["create it"])
        assert result.passed is False
        assert result.fixes == ["create it"]


class TestCheckAutoDir:
    def test_existing_dir(self, tmp_path):
        d = tmp_path / "existing"
        d.mkdir()
        result = check_auto_dir(d, "existing")
        assert result.passed is True

    def test_auto_creates_dir(self, tmp_path):
        d = tmp_path / "new_dir"
        result = check_auto_dir(d, "new_dir")
        assert result.passed is True
        assert d.exists()
        assert "auto-created" in result.label


class TestCheckFonts:
    def test_fonts_dir_missing_uses_packaged_fonts(self, tmp_path):
        paths = ProjectPaths(root=tmp_path)
        assert paths.fonts_dir.parent.name == "assets"       # falls back to the fonts shipped in the package
        assert check_fonts(paths).passed is True

    def test_fonts_dir_empty(self, tmp_path):
        fonts = tmp_path / "fonts"
        fonts.mkdir()
        paths = ProjectPaths(root=tmp_path)
        result = check_fonts(paths)
        assert result.passed is False

    def test_fonts_dir_with_files(self, tmp_path):
        fonts = tmp_path / "fonts"
        fonts.mkdir()
        (fonts / "font.woff2").write_text("font data")
        paths = ProjectPaths(root=tmp_path)
        result = check_fonts(paths)
        assert result.passed is True
