"""Tests for job description extraction."""

import pytest

from applyloop.jd_extractor import (
    ExtractedJD,
    _detect_ats,
    _split_sections,
    extract_jd_from_text,
)


class TestDetectAts:
    def test_greenhouse(self):
        assert _detect_ats("https://boards.greenhouse.io/company/jobs/123") == "greenhouse"

    def test_lever(self):
        assert _detect_ats("https://jobs.lever.co/company/abc-123") == "lever"

    def test_ashby(self):
        assert _detect_ats("https://jobs.ashbyhq.com/company/job-id") == "ashby"

    def test_workday(self):
        assert _detect_ats("https://company.wd5.myworkdayjobs.com/en/careers") == "workday"

    def test_unknown(self):
        assert _detect_ats("https://example.com/careers") == "unknown"

    def test_case_insensitive(self):
        assert _detect_ats("https://BOARDS.GREENHOUSE.IO/company") == "greenhouse"


class TestSplitSections:
    def test_splits_on_requirements(self):
        jd = ExtractedJD(url="", full_text="About the role. Great job.\n\nRequirements\n- Python\n- Django")
        _split_sections(jd)
        assert "About the role" in jd.description
        assert "Python" in jd.requirements

    def test_splits_on_qualifications(self):
        jd = ExtractedJD(url="", full_text="We're hiring!\n\nQualifications\n- 5 years exp")
        _split_sections(jd)
        assert "hiring" in jd.description
        assert "5 years" in jd.requirements

    def test_splits_on_what_we_look_for(self):
        jd = ExtractedJD(url="", full_text="Great company.\n\nWhat we're looking for\n- Skills")
        _split_sections(jd)
        assert "Great company" in jd.description
        assert "Skills" in jd.requirements

    def test_no_requirements_section(self):
        jd = ExtractedJD(url="", full_text="Just a plain job description")
        _split_sections(jd)
        assert jd.description == "Just a plain job description"
        assert jd.requirements == ""

    def test_empty_text(self):
        jd = ExtractedJD(url="", full_text="")
        _split_sections(jd)
        assert jd.description == ""
        assert jd.requirements == ""


class TestExtractJdFromText:
    def test_basic_extraction(self):
        jd = extract_jd_from_text("Senior Python Developer needed", company="Acme", title="Python Dev")
        assert jd.company == "Acme"
        assert jd.title == "Python Dev"
        assert "Python" in jd.full_text
        assert jd.url == ""

    def test_empty_text(self):
        jd = extract_jd_from_text("")
        assert jd.full_text == ""

    def test_splits_requirements(self):
        text = "We're hiring.\n\nRequirements\n- Python\n- Django"
        jd = extract_jd_from_text(text)
        assert "Requirements" in jd.requirements
