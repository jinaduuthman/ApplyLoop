"""Tests for the applicant module."""

import json
import pytest
from pathlib import Path

from applyloop.applicant import (
    ApplicationResult,
    _match_known_answer,
    save_application_log,
)
from applyloop.models import CandidateProfile


@pytest.fixture
def sample_profile():
    return CandidateProfile(
        full_name="Jane Smith",
        email="jane@example.com",
        phone="+1-555-0123",
        location="San Francisco, CA",
        linkedin="linkedin.com/in/janesmith",
        portfolio_url="https://janesmith.dev",
        github="github.com/janesmith",
        twitter="https://x.com/janesmith",
        headline="ML Engineer",
        superpowers=["ML pipelines", "Fast prototyping"],
        target_roles=["ML Engineer", "AI Engineer"],
        target_comp="$150K-200K",
        country="United States",
        city="San Francisco",
        timezone="PST",
    )


class TestApplicationResult:
    def test_to_dict(self):
        result = ApplicationResult(
            url="https://example.com/apply",
            company="Acme",
            role="Engineer",
            status="submitted",
            platform="greenhouse",
            fields_filled=["name", "email", "resume"],
            timestamp="2024-03-15T10:00:00",
        )
        d = result.to_dict()
        assert d["url"] == "https://example.com/apply"
        assert d["company"] == "Acme"
        assert d["status"] == "submitted"
        assert d["platform"] == "greenhouse"
        assert len(d["fields_filled"]) == 3

    def test_default_values(self):
        result = ApplicationResult(url="u", company="c", role="r", status="failed")
        assert result.platform == ""
        assert result.error == ""
        assert result.fields_filled == []


class TestMatchKnownAnswer:
    def test_salary_question(self, sample_profile):
        ans = _match_known_answer("What is your expected salary?", sample_profile)
        assert ans == "$150K-200K"

    def test_compensation_question(self, sample_profile):
        ans = _match_known_answer("Desired compensation range", sample_profile)
        assert ans == "$150K-200K"

    def test_location_question(self, sample_profile):
        ans = _match_known_answer("Where are you based?", sample_profile)
        assert "San Francisco" in ans

    def test_linkedin_question(self, sample_profile):
        ans = _match_known_answer("LinkedIn profile URL", sample_profile)
        assert "linkedin.com" in ans

    def test_github_question(self, sample_profile):
        ans = _match_known_answer("GitHub username", sample_profile)
        assert "github.com" in ans

    def test_portfolio_question(self, sample_profile):
        ans = _match_known_answer("Portfolio or website", sample_profile)
        assert "janesmith.dev" in ans

    def test_work_authorization(self, sample_profile):
        ans = _match_known_answer("Are you authorized to work in the US?", sample_profile)
        assert ans == "Yes"

    def test_sponsorship(self, sample_profile):
        ans = _match_known_answer("Do you require visa sponsorship?", sample_profile)
        assert ans == "No"

    def test_unknown_question(self, sample_profile):
        ans = _match_known_answer("Tell us about your favorite hobby", sample_profile)
        assert ans == ""

    def test_empty_profile_field(self):
        profile = CandidateProfile()
        ans = _match_known_answer("LinkedIn URL", profile)
        assert ans == ""


class TestSaveApplicationLog:
    def test_creates_new_file(self, tmp_path):
        results = [
            ApplicationResult(
                url="https://a.com", company="A", role="Eng",
                status="submitted", timestamp="2024-01-01",
            ),
        ]
        out = tmp_path / "log.json"
        save_application_log(results, out)

        data = json.loads(out.read_text())
        assert len(data) == 1
        assert data[0]["company"] == "A"

    def test_appends_to_existing(self, tmp_path):
        out = tmp_path / "log.json"
        out.write_text(json.dumps([
            {"url": "old", "company": "Old", "role": "X", "status": "submitted"},
        ]))

        results = [
            ApplicationResult(
                url="https://b.com", company="B", role="Eng",
                status="submitted", timestamp="2024-01-02",
            ),
        ]
        save_application_log(results, out)

        data = json.loads(out.read_text())
        assert len(data) == 2
        assert data[0]["company"] == "Old"
        assert data[1]["company"] == "B"

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "sub" / "dir" / "log.json"
        results = [
            ApplicationResult(url="u", company="C", role="R", status="failed"),
        ]
        save_application_log(results, out)
        assert out.exists()

    def test_handles_corrupt_existing(self, tmp_path):
        out = tmp_path / "log.json"
        out.write_text("not valid json")

        results = [
            ApplicationResult(url="u", company="C", role="R", status="submitted"),
        ]
        save_application_log(results, out)

        data = json.loads(out.read_text())
        assert len(data) == 1
