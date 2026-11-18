"""Tests for keyword gap analysis."""

import pytest

from applyloop.keywords import analyze_keywords, _extract_tech_keywords


class TestExtractTechKeywords:
    def test_finds_python(self):
        result = _extract_tech_keywords("We need a Python developer")
        assert "python" in result

    def test_finds_multiple_techs(self):
        result = _extract_tech_keywords("Python, Django, PostgreSQL, Docker, AWS")
        assert "python" in result
        assert "django" in result
        assert "postgresql" in result
        assert "docker" in result
        assert "aws" in result

    def test_case_insensitive(self):
        result = _extract_tech_keywords("PYTHON and REACT")
        assert "python" in result
        assert "react" in result

    def test_empty_text(self):
        result = _extract_tech_keywords("")
        assert len(result) == 0


class TestAnalyzeKeywords:
    def test_basic_analysis(self):
        jd = "We need a Python developer with Django experience and PostgreSQL skills"
        cv = "I am a Python developer experienced in Flask, FastAPI, and MySQL"
        result = analyze_keywords(jd, cv)

        assert "python" in result["tech_match"]
        assert result["match_percentage"] > 0

    def test_all_matching(self):
        jd = "Python Django PostgreSQL"
        cv = "Python Django PostgreSQL"
        result = analyze_keywords(jd, cv)
        assert len(result["tech_missing"]) == 0

    def test_all_missing(self):
        jd = "Rust Kubernetes Terraform"
        cv = "Python Django PostgreSQL"
        result = analyze_keywords(jd, cv)
        assert "rust" in result["tech_missing"]
        assert "kubernetes" in result["tech_missing"]

    def test_compound_terms(self):
        jd = "Experience in machine learning and deep learning required"
        cv = "I have experience in machine learning projects"
        result = analyze_keywords(jd, cv)
        assert "deep learning" in result["compound_missing"]
        assert "machine learning" not in result["compound_missing"]

    def test_empty_jd(self):
        result = analyze_keywords("", "Python developer")
        assert result["match_percentage"] == 0

    def test_empty_cv(self):
        result = analyze_keywords("Python developer", "")
        assert len(result["tech_missing"]) > 0

    def test_returns_all_expected_keys(self):
        result = analyze_keywords("Python", "Python")
        expected_keys = [
            "jd_keywords", "cv_keywords", "missing", "matching",
            "match_percentage", "compound_missing", "tech_in_jd",
            "tech_in_cv", "tech_missing", "tech_match",
        ]
        for key in expected_keys:
            assert key in result
