"""Tests for the job_finder module."""

import pytest
from pathlib import Path

from applyloop.job_finder import (
    FoundJob,
    _deduplicate_jobs,
    _title_matches_profile,
    save_found_jobs,
)


class TestTitleMatchesProfile:
    def test_exact_match(self):
        assert _title_matches_profile("Senior ML Engineer", ["Senior ML Engineer"])

    def test_partial_match(self):
        assert _title_matches_profile("Staff Machine Learning Engineer", ["ML Engineer"])

    def test_no_match(self):
        assert not _title_matches_profile("Product Designer", ["ML Engineer", "Data Scientist"])

    def test_case_insensitive(self):
        assert _title_matches_profile("senior data scientist", ["Data Scientist"])

    def test_empty_target_roles(self):
        assert not _title_matches_profile("Engineer", [])

    def test_short_words_ignored(self):
        assert not _title_matches_profile("ML Ops Lead", ["AI Ops"])

    def test_single_significant_word_match(self):
        assert _title_matches_profile("Python Engineer", ["Engineer"])


class TestDeduplicateJobs:
    def test_removes_exact_duplicates(self):
        jobs = [
            FoundJob(url="https://example.com/jobs/1", title="Eng", company="A"),
            FoundJob(url="https://example.com/jobs/1", title="Eng", company="A"),
        ]
        result = _deduplicate_jobs(jobs)
        assert len(result) == 1

    def test_normalizes_trailing_slash(self):
        jobs = [
            FoundJob(url="https://example.com/jobs/1", title="Eng", company="A"),
            FoundJob(url="https://example.com/jobs/1/", title="Eng", company="A"),
        ]
        result = _deduplicate_jobs(jobs)
        assert len(result) == 1

    def test_strips_query_params(self):
        jobs = [
            FoundJob(url="https://example.com/jobs/1", title="Eng", company="A"),
            FoundJob(url="https://example.com/jobs/1?ref=search", title="Eng", company="A"),
        ]
        result = _deduplicate_jobs(jobs)
        assert len(result) == 1

    def test_keeps_different_urls(self):
        jobs = [
            FoundJob(url="https://example.com/jobs/1", title="Eng", company="A"),
            FoundJob(url="https://example.com/jobs/2", title="SRE", company="B"),
        ]
        result = _deduplicate_jobs(jobs)
        assert len(result) == 2

    def test_empty_list(self):
        assert _deduplicate_jobs([]) == []

    def test_case_insensitive_urls(self):
        jobs = [
            FoundJob(url="https://Example.com/Jobs/1", title="Eng", company="A"),
            FoundJob(url="https://example.com/jobs/1", title="Eng", company="A"),
        ]
        result = _deduplicate_jobs(jobs)
        assert len(result) == 1


class TestFoundJob:
    def test_to_scanned(self):
        job = FoundJob(
            url="https://example.com/1",
            title="ML Engineer",
            company="Acme",
            location="Remote",
            source="indeed",
            is_new=True,
        )
        scanned = job.to_scanned()
        assert scanned.url == "https://example.com/1"
        assert scanned.title == "ML Engineer"
        assert scanned.company == "Acme"
        assert scanned.source == "indeed"
        assert scanned.is_new is True

    def test_default_values(self):
        job = FoundJob(url="https://x.com", title="Eng", company="A")
        assert job.location == ""
        assert job.source == ""
        assert job.score == 0.0
        assert job.is_new is True


class TestSaveFoundJobs:
    def test_saves_to_file(self, tmp_path):
        jobs = [
            FoundJob(url="https://a.com/1", title="Eng A", company="CompA"),
            FoundJob(url="https://b.com/2", title="Eng B", company="CompB"),
            FoundJob(url="", title="No URL", company="CompC"),
        ]
        out = tmp_path / "jobs.txt"
        save_found_jobs(jobs, out)

        content = out.read_text()
        assert "https://a.com/1" in content
        assert "https://b.com/2" in content
        assert "CompA" in content
        assert "CompB" in content
        # Jobs without URLs should be skipped
        assert "No URL" not in content

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "sub" / "dir" / "jobs.txt"
        save_found_jobs([FoundJob(url="https://x.com", title="T", company="C")], out)
        assert out.exists()

    def test_header_includes_count(self, tmp_path):
        jobs = [FoundJob(url="https://x.com", title="T", company="C")]
        out = tmp_path / "jobs.txt"
        save_found_jobs(jobs, out)
        content = out.read_text()
        assert "Total: 1" in content
