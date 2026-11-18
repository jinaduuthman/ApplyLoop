"""Tests for the autopilot module."""

import pytest

from applyloop.autopilot import AutopilotConfig, AutopilotResult


class TestAutopilotConfig:
    def test_defaults(self):
        cfg = AutopilotConfig()
        assert cfg.min_score == 3.5
        assert cfg.max_applications == 10
        assert cfg.auto_submit is False
        assert cfg.generate_cover is True
        assert cfg.skip_tailor is False
        assert cfg.skip_pdf is False
        assert cfg.cooldown_seconds == 30
        assert "indeed" in cfg.sources
        assert "linkedin" in cfg.sources
        assert cfg.include_portals is True
        assert cfg.dry_run is False

    def test_custom_values(self):
        cfg = AutopilotConfig(
            min_score=4.0,
            max_applications=5,
            auto_submit=True,
            cooldown_seconds=60,
            dry_run=True,
        )
        assert cfg.min_score == 4.0
        assert cfg.max_applications == 5
        assert cfg.auto_submit is True
        assert cfg.cooldown_seconds == 60
        assert cfg.dry_run is True

    def test_from_kwargs(self):
        cfg = AutopilotConfig.from_kwargs(
            min_score=4.0,
            max_applications=3,
            unknown_field="ignored",
        )
        assert cfg.min_score == 4.0
        assert cfg.max_applications == 3

    def test_from_kwargs_ignores_none(self):
        cfg = AutopilotConfig.from_kwargs(
            min_score=None,
            max_applications=5,
        )
        assert cfg.min_score == 3.5  # default
        assert cfg.max_applications == 5


class TestAutopilotResult:
    def test_defaults(self):
        result = AutopilotResult()
        assert result.jobs_found == 0
        assert result.jobs_evaluated == 0
        assert result.jobs_above_threshold == 0
        assert result.jobs_applied == 0
        assert result.jobs_submitted == 0
        assert result.jobs_failed == 0
        assert result.jobs_skipped == 0
        assert result.evaluations == []
        assert result.applications == []

    def test_with_values(self):
        result = AutopilotResult(
            jobs_found=50,
            jobs_evaluated=20,
            jobs_above_threshold=8,
            jobs_applied=8,
            jobs_submitted=6,
            jobs_failed=1,
            jobs_skipped=1,
        )
        assert result.jobs_found == 50
        assert result.jobs_submitted == 6
        assert result.jobs_failed + result.jobs_skipped + result.jobs_submitted == 8
