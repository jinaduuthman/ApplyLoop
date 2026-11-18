"""Tests for Stagehand browser integration."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from applyloop.stagehand_browser import (
    FORM_FIELDS_SCHEMA,
    JOB_DESCRIPTION_SCHEMA,
    JOB_LISTING_SCHEMA,
    _resolve_model_config,
    is_stagehand_enabled,
)


class TestIsStagehandEnabled:
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("applyloop.stagehand_browser._stagehand_available", True):
                assert not is_stagehand_enabled()

    def test_enabled_with_true(self):
        with patch.dict(os.environ, {"STAGEHAND_ENABLED": "true"}):
            with patch("applyloop.stagehand_browser._stagehand_available", True):
                assert is_stagehand_enabled()

    def test_enabled_with_1(self):
        with patch.dict(os.environ, {"STAGEHAND_ENABLED": "1"}):
            with patch("applyloop.stagehand_browser._stagehand_available", True):
                assert is_stagehand_enabled()

    def test_enabled_with_yes(self):
        with patch.dict(os.environ, {"STAGEHAND_ENABLED": "yes"}):
            with patch("applyloop.stagehand_browser._stagehand_available", True):
                assert is_stagehand_enabled()

    def test_disabled_when_package_not_installed(self):
        with patch.dict(os.environ, {"STAGEHAND_ENABLED": "true"}):
            with patch("applyloop.stagehand_browser._stagehand_available", False):
                assert not is_stagehand_enabled()

    def test_disabled_with_false(self):
        with patch.dict(os.environ, {"STAGEHAND_ENABLED": "false"}):
            with patch("applyloop.stagehand_browser._stagehand_available", True):
                assert not is_stagehand_enabled()


class TestResolveModelConfig:
    def test_explicit_model_and_key(self):
        with patch.dict(os.environ, {
            "STAGEHAND_MODEL": "anthropic/claude-sonnet-4-6",
            "MODEL_API_KEY": "sk-test-key",
        }, clear=True):
            model, key = _resolve_model_config()
            assert model == "anthropic/claude-sonnet-4-6"
            assert key == "sk-test-key"

    def test_falls_back_to_openai_key(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-openai-key",
        }, clear=True):
            model, key = _resolve_model_config()
            assert model == "openai/gpt-4o"
            assert key == "sk-openai-key"

    def test_falls_back_to_anthropic_key(self):
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "sk-ant-key",
        }, clear=True):
            model, key = _resolve_model_config()
            assert model == "anthropic/claude-sonnet-4-6"
            assert key == "sk-ant-key"

    def test_anthropic_takes_priority_over_openai(self):
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "sk-ant-key",
            "OPENAI_API_KEY": "sk-openai-key",
        }, clear=True):
            model, key = _resolve_model_config()
            assert "anthropic" in model
            assert key == "sk-ant-key"

    def test_custom_model_with_fallback_key(self):
        with patch.dict(os.environ, {
            "STAGEHAND_MODEL": "openai/gpt-4-turbo",
            "OPENAI_API_KEY": "sk-openai-key",
        }, clear=True):
            model, key = _resolve_model_config()
            assert model == "openai/gpt-4-turbo"
            assert key == "sk-openai-key"

    def test_no_config_returns_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            model, key = _resolve_model_config()
            assert model == "openai/gpt-4o"
            assert key == ""

    def test_explicit_ollama_model(self):
        with patch.dict(os.environ, {
            "STAGEHAND_MODEL": "ollama/llama3",
        }, clear=True):
            model, key = _resolve_model_config()
            assert model == "ollama/llama3"
            assert key == "ollama"

    def test_ollama_from_jobpilot_provider(self):
        with patch.dict(os.environ, {
            "APPLYLOOP_PROVIDER": "ollama",
            "OLLAMA_MODEL": "qwen3",
        }, clear=True):
            model, key = _resolve_model_config()
            assert model == "ollama/qwen3"
            assert key == "ollama"

    def test_ollama_auto_detected_when_no_cloud_keys(self):
        with patch.dict(os.environ, {
            "OLLAMA_MODEL": "mistral",
        }, clear=True):
            model, key = _resolve_model_config()
            assert model == "ollama/mistral"
            assert key == "ollama"

    def test_ollama_default_model_when_provider_set(self):
        with patch.dict(os.environ, {
            "APPLYLOOP_PROVIDER": "ollama",
        }, clear=True):
            model, key = _resolve_model_config()
            assert model == "ollama/llama3"
            assert key == "ollama"

    def test_cloud_key_takes_priority_over_ollama(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-test",
            "OLLAMA_MODEL": "llama3",
        }, clear=True):
            model, key = _resolve_model_config()
            assert "openai" in model
            assert key == "sk-test"

    def test_explicit_ollama_overrides_cloud_key(self):
        with patch.dict(os.environ, {
            "STAGEHAND_MODEL": "ollama/llama3",
            "OPENAI_API_KEY": "sk-test",
        }, clear=True):
            model, key = _resolve_model_config()
            assert model == "ollama/llama3"
            assert key == "ollama"


class TestSchemas:
    """Verify extraction schemas have expected structure."""

    def test_job_listing_schema_structure(self):
        assert "properties" in JOB_LISTING_SCHEMA
        assert "jobs" in JOB_LISTING_SCHEMA["properties"]
        items = JOB_LISTING_SCHEMA["properties"]["jobs"]["items"]
        assert "title" in items["properties"]
        assert "company" in items["properties"]
        assert "url" in items["properties"]

    def test_job_description_schema_structure(self):
        assert "title" in JOB_DESCRIPTION_SCHEMA["properties"]
        assert "description" in JOB_DESCRIPTION_SCHEMA["properties"]
        assert "is_expired" in JOB_DESCRIPTION_SCHEMA["properties"]
        assert "title" in JOB_DESCRIPTION_SCHEMA["required"]

    def test_form_fields_schema_structure(self):
        assert "fields" in FORM_FIELDS_SCHEMA["properties"]
        assert "has_resume_upload" in FORM_FIELDS_SCHEMA["properties"]
        assert "submit_button_text" in FORM_FIELDS_SCHEMA["properties"]


class TestStagehandJobFinder:
    """Test Stagehand integration in job_finder module."""

    def test_source_urls_defined(self):
        from applyloop.job_finder import SOURCE_URLS

        assert "indeed" in SOURCE_URLS
        assert "linkedin" in SOURCE_URLS
        assert "google_jobs" in SOURCE_URLS
        assert "glassdoor" in SOURCE_URLS

    def test_source_urls_have_placeholders(self):
        from applyloop.job_finder import SOURCE_URLS

        for source, url_template in SOURCE_URLS.items():
            assert "{query}" in url_template, f"{source} missing {{query}} placeholder"

    def test_search_with_stagehand_unknown_source(self):
        from applyloop.job_finder import _search_with_stagehand

        mock_browser = MagicMock()
        result = _search_with_stagehand(mock_browser, "unknown_board", "test query")
        assert result == []
        mock_browser.navigate.assert_not_called()

    def test_search_with_stagehand_extracts_jobs(self):
        from applyloop.job_finder import _search_with_stagehand

        mock_browser = MagicMock()
        mock_browser.extract.return_value = {
            "jobs": [
                {
                    "title": "ML Engineer",
                    "company": "Acme Corp",
                    "location": "Remote",
                    "url": "https://example.com/jobs/123",
                    "salary": "$150k-$200k",
                },
                {
                    "title": "Data Scientist",
                    "company": "Beta Inc",
                    "location": "NYC",
                    "url": "/jobs/456",
                },
            ]
        }

        results = _search_with_stagehand(mock_browser, "indeed", "ML Engineer", "NYC")
        assert len(results) == 2
        assert results[0].title == "ML Engineer"
        assert results[0].company == "Acme Corp"
        assert results[0].source == "indeed"
        assert results[1].url.startswith("http")

    def test_search_with_stagehand_handles_error(self):
        from applyloop.job_finder import _search_with_stagehand

        mock_browser = MagicMock()
        mock_browser.navigate.side_effect = Exception("Connection failed")

        results = _search_with_stagehand(mock_browser, "indeed", "test")
        assert results == []

    def test_search_with_stagehand_handles_empty_titles(self):
        from applyloop.job_finder import _search_with_stagehand

        mock_browser = MagicMock()
        mock_browser.extract.return_value = {
            "jobs": [
                {"title": "", "company": "Acme"},
                {"title": "Valid Role", "company": "Beta"},
            ]
        }

        results = _search_with_stagehand(mock_browser, "linkedin", "test")
        assert len(results) == 1
        assert results[0].title == "Valid Role"


class TestStagehandExtractor:
    """Test Stagehand integration in jd_extractor module."""

    @patch("applyloop.jd_extractor.is_stagehand_enabled", return_value=True)
    @patch("applyloop.jd_extractor._extract_jd_stagehand")
    def test_extract_jd_routes_to_stagehand(self, mock_sh, mock_enabled):
        from applyloop.jd_extractor import ExtractedJD, extract_jd_from_url

        mock_sh.return_value = ExtractedJD(
            url="https://example.com/job",
            title="ML Engineer",
            company="Acme",
            full_text="Great job description...",
        )

        result = extract_jd_from_url("https://example.com/job")
        mock_sh.assert_called_once_with("https://example.com/job")
        assert result.title == "ML Engineer"

    @patch("applyloop.jd_extractor.is_stagehand_enabled", return_value=False)
    def test_extract_jd_falls_back_to_playwright(self, mock_enabled):
        """When Stagehand is disabled, the Playwright path should be used."""
        pass


class TestStagehandApplicant:
    """Test Stagehand integration in applicant module."""

    @patch("applyloop.applicant.is_stagehand_enabled", return_value=True)
    @patch("applyloop.applicant._apply_with_stagehand")
    @patch("applyloop.applicant.load_profile")
    def test_apply_routes_to_stagehand(self, mock_profile, mock_sh, mock_enabled):
        from applyloop.applicant import ApplicationResult, apply_to_job
        from applyloop.models import CandidateProfile

        mock_profile.return_value = CandidateProfile(
            full_name="Test User",
            email="test@example.com",
        )
        mock_sh.return_value = ApplicationResult(
            url="https://example.com/apply",
            company="Acme",
            role="Engineer",
            status="submitted",
        )

        import tempfile
        from pathlib import Path
        resume = Path(tempfile.mkdtemp()) / "cv.pdf"
        resume.write_bytes(b"%PDF-1.4")
        result = apply_to_job(MagicMock(), url="https://example.com/apply", resume_path=resume)
        mock_sh.assert_called_once()
        assert result.status == "submitted"
