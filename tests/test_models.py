"""Tests for data models."""

import pytest

from applyloop.models import (
    Application,
    ApplicationStatus,
    CandidateProfile,
    Reminder,
    role_fuzzy_match,
)


class TestApplicationStatus:
    def test_from_canonical_string(self):
        assert ApplicationStatus.from_string("Applied") == ApplicationStatus.APPLIED
        assert ApplicationStatus.from_string("Evaluated") == ApplicationStatus.EVALUATED
        assert ApplicationStatus.from_string("Interview") == ApplicationStatus.INTERVIEW
        assert ApplicationStatus.from_string("Offer") == ApplicationStatus.OFFER
        assert ApplicationStatus.from_string("Rejected") == ApplicationStatus.REJECTED
        assert ApplicationStatus.from_string("Discarded") == ApplicationStatus.DISCARDED
        assert ApplicationStatus.from_string("SKIP") == ApplicationStatus.SKIP

    def test_from_spanish_aliases(self):
        assert ApplicationStatus.from_string("aplicado") == ApplicationStatus.APPLIED
        assert ApplicationStatus.from_string("evaluada") == ApplicationStatus.EVALUATED
        assert ApplicationStatus.from_string("entrevista") == ApplicationStatus.INTERVIEW
        assert ApplicationStatus.from_string("oferta") == ApplicationStatus.OFFER
        assert ApplicationStatus.from_string("rechazado") == ApplicationStatus.REJECTED
        assert ApplicationStatus.from_string("descartado") == ApplicationStatus.DISCARDED
        assert ApplicationStatus.from_string("no aplicar") == ApplicationStatus.SKIP

    def test_from_alternate_aliases(self):
        assert ApplicationStatus.from_string("enviada") == ApplicationStatus.APPLIED
        assert ApplicationStatus.from_string("sent") == ApplicationStatus.APPLIED
        assert ApplicationStatus.from_string("cerrada") == ApplicationStatus.DISCARDED
        assert ApplicationStatus.from_string("cancelada") == ApplicationStatus.DISCARDED
        assert ApplicationStatus.from_string("skip") == ApplicationStatus.SKIP
        assert ApplicationStatus.from_string("monitor") == ApplicationStatus.SKIP

    def test_from_duplicado_patterns(self):
        assert ApplicationStatus.from_string("duplicado #123") == ApplicationStatus.DISCARDED
        assert ApplicationStatus.from_string("dup") == ApplicationStatus.DISCARDED
        assert ApplicationStatus.from_string("repost #45") == ApplicationStatus.DISCARDED

    def test_strips_markdown_bold(self):
        assert ApplicationStatus.from_string("**Applied**") == ApplicationStatus.APPLIED

    def test_strips_trailing_dates(self):
        assert ApplicationStatus.from_string("Applied 2024-01-15") == ApplicationStatus.APPLIED

    def test_unknown_defaults_to_evaluated(self):
        assert ApplicationStatus.from_string("something_random") == ApplicationStatus.EVALUATED

    def test_case_insensitive(self):
        assert ApplicationStatus.from_string("APPLIED") == ApplicationStatus.APPLIED
        assert ApplicationStatus.from_string("applied") == ApplicationStatus.APPLIED
        assert ApplicationStatus.from_string("ApPlIeD") == ApplicationStatus.APPLIED

    def test_rank_ordering(self):
        assert ApplicationStatus.OFFER.rank > ApplicationStatus.INTERVIEW.rank
        assert ApplicationStatus.INTERVIEW.rank > ApplicationStatus.RESPONDED.rank
        assert ApplicationStatus.RESPONDED.rank > ApplicationStatus.APPLIED.rank
        assert ApplicationStatus.APPLIED.rank > ApplicationStatus.EVALUATED.rank
        assert ApplicationStatus.EVALUATED.rank > ApplicationStatus.REJECTED.rank
        assert ApplicationStatus.SKIP.rank == 0
        assert ApplicationStatus.DISCARDED.rank == 0


class TestApplication:
    SAMPLE_LINE = "| 42 | 2024-03-15 | Acme Corp | Senior Engineer | 3.8/5 | Applied | cv.pdf | [42](reports/042-acme.md) | Great culture |"

    def test_from_pipe_line_valid(self):
        app = Application.from_pipe_line(self.SAMPLE_LINE)
        assert app is not None
        assert app.num == 42
        assert app.date == "2024-03-15"
        assert app.company == "Acme Corp"
        assert app.role == "Senior Engineer"
        assert app.score == "3.8/5"
        assert app.status == "Applied"
        assert app.notes == "Great culture"

    def test_from_pipe_line_too_few_columns(self):
        assert Application.from_pipe_line("| 1 | 2024-01-01 |") is None

    def test_from_pipe_line_non_numeric_num(self):
        assert Application.from_pipe_line("| abc | 2024-01-01 | Co | Role | 3/5 | Applied | x | y |") is None

    def test_from_pipe_line_zero_num(self):
        assert Application.from_pipe_line("| 0 | 2024-01-01 | Co | Role | 3/5 | Applied | x | y |") is None

    def test_from_pipe_line_header_row(self):
        assert Application.from_pipe_line("| # | Date | Company | Role | Score | Status | PDF | Report |") is None

    def test_parsed_score(self):
        app = Application.from_pipe_line(self.SAMPLE_LINE)
        assert app.parsed_score == 3.8

    def test_parsed_score_bold(self):
        line = "| 1 | 2024-01-01 | Co | Role | **4.2/5** | Applied | x | y |"
        app = Application.from_pipe_line(line)
        assert app.parsed_score == 4.2

    def test_parsed_score_na(self):
        line = "| 1 | 2024-01-01 | Co | Role | N/A | Applied | x | y |"
        app = Application.from_pipe_line(line)
        assert app.parsed_score == 0.0

    def test_report_num(self):
        app = Application.from_pipe_line(self.SAMPLE_LINE)
        assert app.report_num == 42

    def test_report_num_none(self):
        line = "| 1 | 2024-01-01 | Co | Role | 3/5 | Applied | x | no-report |"
        app = Application.from_pipe_line(line)
        assert app.report_num is None

    def test_normalized_company(self):
        app = Application.from_pipe_line(self.SAMPLE_LINE)
        assert app.normalized_company == "acmecorp"

    def test_canonical_status(self):
        app = Application.from_pipe_line(self.SAMPLE_LINE)
        assert app.canonical_status == ApplicationStatus.APPLIED

    def test_to_pipe_line(self):
        app = Application.from_pipe_line(self.SAMPLE_LINE)
        result = app.to_pipe_line()
        assert "| 42 |" in result
        assert "Acme Corp" in result

    def test_parsed_date(self):
        app = Application.from_pipe_line(self.SAMPLE_LINE)
        d = app.parsed_date
        assert d is not None
        assert d.year == 2024
        assert d.month == 3
        assert d.day == 15

    def test_parsed_date_invalid(self):
        line = "| 1 | not-a-date | Co | Role | 3/5 | Applied | x | y |"
        app = Application.from_pipe_line(line)
        assert app.parsed_date is None


class TestRoleFuzzyMatch:
    def test_matching_roles(self):
        assert role_fuzzy_match("Senior Software Engineer", "Senior Software Eng")
        assert role_fuzzy_match("ML Engineer - Platform", "Machine Learning Engineer Platform")
        assert role_fuzzy_match("Staff Backend Engineer", "Senior Backend Engineer")

    def test_non_matching_roles(self):
        assert not role_fuzzy_match("CEO", "Junior Intern")
        assert not role_fuzzy_match("Designer", "Engineer")

    def test_short_words_ignored(self):
        assert not role_fuzzy_match("ML Eng", "AI Dev")

    def test_empty_strings(self):
        assert not role_fuzzy_match("", "")
        assert not role_fuzzy_match("Engineer", "")


class TestCandidateProfile:
    def test_from_yaml(self):
        data = {
            "candidate": {
                "full_name": "Jane Smith",
                "email": "jane@example.com",
                "location": "SF",
            },
            "narrative": {
                "headline": "ML Engineer",
                "superpowers": ["Python", "ML"],
            },
            "target_roles": {
                "primary": ["Senior ML Engineer"],
                "archetypes": [
                    {"name": "ML Engineer", "level": "Senior", "fit": "primary"},
                ],
            },
            "compensation": {
                "target_range": "$150K",
                "currency": "USD",
            },
            "location": {
                "country": "US",
                "city": "SF",
            },
        }
        profile = CandidateProfile.from_yaml(data)
        assert profile.full_name == "Jane Smith"
        assert profile.headline == "ML Engineer"
        assert len(profile.archetypes) == 1
        assert profile.archetypes[0].fit == "primary"
        assert profile.target_comp == "$150K"

    def test_from_empty_yaml(self):
        profile = CandidateProfile.from_yaml({})
        assert profile.full_name == ""
        assert profile.archetypes == []


class TestReminder:
    def test_round_trip(self):
        r = Reminder(
            application_num=1,
            company="Acme",
            role="Engineer",
            remind_date="2024-04-01",
            reminder_type="follow_up",
            message="Check in",
        )
        d = r.to_dict()
        r2 = Reminder.from_dict(d)
        assert r2.application_num == 1
        assert r2.company == "Acme"
        assert r2.remind_date == "2024-04-01"
        assert r2.completed is False
