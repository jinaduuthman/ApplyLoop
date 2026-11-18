"""Tests for data export."""

import csv
import json
import pytest
from pathlib import Path

from applyloop.config import ProjectPaths
from applyloop.export import export_csv, export_json, export_excel


@pytest.fixture
def sample_project(tmp_path):
    apps = tmp_path / "data" / "applications.md"
    apps.parent.mkdir(parents=True)
    apps.write_text(
        "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| 1 | 2024-01-15 | Acme | Engineer | 3.5/5 | Applied | cv.pdf | [1](r.md) | good |\n"
        "| 2 | 2024-01-16 | Beta | Designer | 4.0/5 | Evaluated | cv.pdf | [2](r.md) | |\n"
    )
    (tmp_path / "output").mkdir()
    return ProjectPaths(root=tmp_path)


class TestExportCsv:
    def test_creates_csv(self, sample_project):
        out = export_csv(sample_project)
        assert out.exists()
        assert out.suffix == ".csv"

        with open(out) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["company"] == "Acme"
        assert rows[1]["company"] == "Beta"

    def test_custom_output_path(self, sample_project, tmp_path):
        out_path = tmp_path / "custom.csv"
        out = export_csv(sample_project, output=out_path)
        assert out == out_path
        assert out.exists()

    def test_empty_data(self, tmp_path):
        paths = ProjectPaths(root=tmp_path)
        (tmp_path / "output").mkdir(parents=True)
        out = export_csv(paths)
        with open(out) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 0


class TestExportJson:
    def test_creates_json(self, sample_project):
        out = export_json(sample_project)
        assert out.exists()

        data = json.loads(out.read_text())
        assert data["total"] == 2
        assert len(data["entries"]) == 2
        assert data["entries"][0]["company"] == "Acme"

    def test_includes_numeric_score(self, sample_project):
        out = export_json(sample_project)
        data = json.loads(out.read_text())
        assert data["entries"][0]["score_numeric"] == 3.5


class TestExportExcel:
    def test_creates_xlsx(self, sample_project):
        out = export_excel(sample_project)
        assert out.exists()
        assert out.suffix == ".xlsx"

    def test_xlsx_has_data(self, sample_project):
        from openpyxl import load_workbook
        out = export_excel(sample_project)
        wb = load_workbook(str(out))
        ws = wb.active
        assert ws.cell(row=1, column=1).value == "#"
        assert ws.cell(row=2, column=3).value == "Acme"
        assert ws.cell(row=3, column=3).value == "Beta"
