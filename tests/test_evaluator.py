"""Tests for the evaluation engine."""

import json
import pytest

from applyloop.evaluator import (
    DimensionScore,
    EvaluationResult,
    _parse_ai_response,
    _score_to_grade,
    generate_report_markdown,
)


class TestScoreToGrade:
    def test_grade_a(self):
        assert _score_to_grade(4.5) == "A"
        assert _score_to_grade(5.0) == "A"

    def test_grade_a_minus(self):
        assert _score_to_grade(4.0) == "A-"
        assert _score_to_grade(4.4) == "A-"

    def test_grade_b_plus(self):
        assert _score_to_grade(3.5) == "B+"
        assert _score_to_grade(3.9) == "B+"

    def test_grade_b(self):
        assert _score_to_grade(3.0) == "B"
        assert _score_to_grade(3.4) == "B"

    def test_grade_c(self):
        assert _score_to_grade(2.0) == "C"

    def test_grade_d(self):
        assert _score_to_grade(1.5) == "D"

    def test_grade_f(self):
        assert _score_to_grade(0.5) == "F"
        assert _score_to_grade(0.0) == "F"


class TestParseAiResponse:
    def test_plain_json(self):
        data = _parse_ai_response('{"company": "Acme", "role": "Eng"}')
        assert data["company"] == "Acme"

    def test_json_with_fences(self):
        data = _parse_ai_response('```json\n{"company": "Acme"}\n```')
        assert data["company"] == "Acme"

    def test_json_with_bare_fences(self):
        data = _parse_ai_response('```\n{"role": "Dev"}\n```')
        assert data["role"] == "Dev"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_ai_response("not json at all")


class TestEvaluationResult:
    def test_score_display(self):
        r = EvaluationResult(overall_score=3.8)
        assert r.score_display == "3.8/5"

    def test_status_apply(self):
        r = EvaluationResult(recommendation="apply")
        assert r.status == "Evaluated"

    def test_status_skip(self):
        r = EvaluationResult(recommendation="skip")
        assert r.status == "SKIP"


class TestDimensionScore:
    def test_weighted_score(self):
        d = DimensionScore(name="Tech", score=4.0, weight=0.2)
        assert d.weighted_score == pytest.approx(0.8)

    def test_zero_weight(self):
        d = DimensionScore(name="X", score=5.0, weight=0.0)
        assert d.weighted_score == 0.0


class TestGenerateReportMarkdown:
    def test_contains_key_sections(self):
        result = EvaluationResult(
            company="Acme",
            role="Engineer",
            url="https://example.com/job",
            date="2024-03-15",
            archetype="Backend",
            overall_score=3.8,
            grade="B+",
            recommendation="apply",
            summary="Good fit overall.",
            strengths=["Python expertise", "System design"],
            gaps=["No Go experience"],
            tailored_pitch="Strong backend engineer...",
            interview_prep=["Scaling systems", "Leadership"],
            salary_notes="Within target range",
            dimensions=[
                DimensionScore("Technical Fit", 4.0, 0.2, "Good match"),
                DimensionScore("Seniority", 3.5, 0.12, "Slightly senior"),
            ],
        )

        md = generate_report_markdown(result, 42)
        assert "# Evaluation Report #042" in md
        assert "Acme" in md
        assert "Engineer" in md
        assert "3.8/5" in md
        assert "B+" in md
        assert "APPLY" in md
        assert "Python expertise" in md
        assert "No Go experience" in md
        assert "STAR" in md
        assert "Compensation" in md

    def test_handles_empty_result(self):
        result = EvaluationResult()
        md = generate_report_markdown(result, 1)
        assert "# Evaluation Report #001" in md
