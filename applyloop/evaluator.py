"""AI-powered job evaluation engine with structured scoring."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from .config import ProjectPaths, load_profile
from .jd_extractor import ExtractedJD
from .llm import LLM, untrusted

console = Console()


@dataclass
class DimensionScore:
    name: str
    score: float  # 0-5
    weight: float  # 0-1
    reasoning: str = ""

    @property
    def weighted_score(self) -> float:
        return self.score * self.weight


EVALUATION_DIMENSIONS = [
    ("technical_fit", "Technical Fit", 0.20),
    ("seniority_match", "Seniority Match", 0.12),
    ("role_alignment", "Role Alignment", 0.15),
    ("compensation", "Compensation", 0.10),
    ("growth_potential", "Growth Potential", 0.10),
    ("culture_signals", "Culture Signals", 0.08),
    ("location_fit", "Location / Remote", 0.08),
    ("company_stage", "Company Stage & Stability", 0.07),
    ("interview_readiness", "Interview Readiness", 0.05),
    ("strategic_value", "Strategic Career Value", 0.05),
]


@dataclass
class EvaluationResult:
    company: str = ""
    role: str = ""
    url: str = ""
    archetype: str = ""
    overall_score: float = 0.0
    grade: str = ""
    dimensions: list[DimensionScore] = field(default_factory=list)
    summary: str = ""
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    recommendation: str = ""  # "apply", "consider", "skip"
    tailored_pitch: str = ""
    interview_prep: list[str] = field(default_factory=list)
    salary_notes: str = ""
    date: str = ""
    error: str = ""

    @property
    def score_display(self) -> str:
        return f"{self.overall_score:.1f}/5"

    @property
    def status(self) -> str:
        return "SKIP" if self.recommendation == "skip" else "Evaluated"


def _score_to_grade(score: float) -> str:
    if score >= 4.5:
        return "A"
    if score >= 4.0:
        return "A-"
    if score >= 3.5:
        return "B+"
    if score >= 3.0:
        return "B"
    if score >= 2.5:
        return "C+"
    if score >= 2.0:
        return "C"
    if score >= 1.5:
        return "D"
    return "F"


def _build_evaluation_prompt(jd: ExtractedJD, cv_text: str, profile_yaml: str) -> str:
    """Build the prompt sent to the AI for evaluation."""
    return f"""You are a career advisor evaluating a job opportunity for a candidate.

## Candidate CV
{cv_text}

## Candidate Profile
{profile_yaml}

## Job Posting
**Company:** {jd.company}
**Title:** {jd.title}
**Location:** {jd.location}
**URL:** {jd.url}
**ATS Platform:** {jd.ats_platform}

### Full Job Description
{untrusted(jd.full_text, 8000)}

---

Evaluate this job against the candidate's profile. Return ONLY a JSON object (no markdown fences) with this exact structure:

{{
  "company": "{jd.company or 'Unknown'}",
  "role": "{jd.title or 'Unknown Role'}",
  "archetype": "closest matching archetype from candidate profile",
  "dimensions": [
    {{"name": "Technical Fit", "score": 0.0, "reasoning": "..."}},
    {{"name": "Seniority Match", "score": 0.0, "reasoning": "..."}},
    {{"name": "Role Alignment", "score": 0.0, "reasoning": "..."}},
    {{"name": "Compensation", "score": 0.0, "reasoning": "..."}},
    {{"name": "Growth Potential", "score": 0.0, "reasoning": "..."}},
    {{"name": "Culture Signals", "score": 0.0, "reasoning": "..."}},
    {{"name": "Location / Remote", "score": 0.0, "reasoning": "..."}},
    {{"name": "Company Stage & Stability", "score": 0.0, "reasoning": "..."}},
    {{"name": "Interview Readiness", "score": 0.0, "reasoning": "..."}},
    {{"name": "Strategic Career Value", "score": 0.0, "reasoning": "..."}}
  ],
  "summary": "2-3 sentence executive summary of the fit",
  "strengths": ["strength 1", "strength 2", "strength 3"],
  "gaps": ["gap 1", "gap 2"],
  "recommendation": "apply or consider or skip",
  "tailored_pitch": "2-3 sentences the candidate could use in a cover letter or intro email",
  "interview_prep": ["STAR story topic 1", "STAR story topic 2", "STAR story topic 3"],
  "salary_notes": "compensation analysis vs candidate target"
}}

Each dimension score must be 0.0-5.0. Be honest and calibrated — a 3.0 is average fit, 4.0+ is strong.
Only recommend "apply" for scores 3.5+. Recommend "skip" for scores below 2.5."""


def _legacy(provider: str, model: str, api_key: str, prompt: str) -> str:
    """Legacy helpers now delegate to the unified client (handles new OpenAI params, retries)."""
    from .llm import BASE_URLS, LLM, LLMConfig, resolve_config

    cfg = resolve_config()
    cfg = LLMConfig(provider=provider, model=model or cfg.model, api_key=api_key,
                    base_url=(os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/v1")
                    if provider == "ollama" else BASE_URLS.get(provider, ""))
    return LLM(cfg).complete(prompt)


def _call_anthropic(prompt: str, api_key: str) -> str:
    return _legacy("anthropic", os.environ.get("APPLYLOOP_MODEL", "claude-sonnet-5-5"), api_key, prompt)


def _call_openai(prompt: str, api_key: str, model: str = "") -> str:
    return _legacy("openai", model, api_key, prompt)


def _call_ollama(prompt: str, model: str = "llama3", base_url: str = "") -> str:
    return _legacy("ollama", model, "", prompt)


def _parse_ai_response(raw: str) -> dict:
    """Parse the AI's JSON response, stripping markdown fences if present."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _get_ai_provider() -> tuple[str, str, str]:
    """Detect which AI provider to use from environment variables.

    Returns (provider, api_key, model).
    Provider priority: explicit APPLYLOOP_PROVIDER > Anthropic > OpenAI > Ollama.
    """
    explicit = os.environ.get("APPLYLOOP_PROVIDER", "").lower()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    ollama_model = os.environ.get("OLLAMA_MODEL", "")

    if explicit == "ollama" or (ollama_model and not anthropic_key and not openai_key):
        model = os.environ.get("APPLYLOOP_MODEL", ollama_model or "llama3")
        return "ollama", "", model

    if explicit == "anthropic" or (anthropic_key and explicit != "openai"):
        model = os.environ.get("APPLYLOOP_MODEL", "claude-sonnet-5-5")
        return "anthropic", anthropic_key, model

    if openai_key:
        model = os.environ.get("APPLYLOOP_MODEL", "")
        return "openai", openai_key, model

    if ollama_model:
        model = os.environ.get("APPLYLOOP_MODEL", ollama_model)
        return "ollama", "", model

    return "", "", ""


def recommendation_for(score: float) -> str:
    """The computed score decides — the model's own label can't contradict it."""
    return "apply" if score >= 3.5 else "consider" if score >= 2.5 else "skip"


def result_from_data(result: EvaluationResult, data: dict, jd: ExtractedJD) -> EvaluationResult:
    result.company = data.get("company") or jd.company
    result.role = data.get("role") or jd.title
    result.archetype = data.get("archetype", "")
    result.summary = data.get("summary", "")
    result.strengths = data.get("strengths", []) or []
    result.gaps = data.get("gaps", []) or []
    result.tailored_pitch = data.get("tailored_pitch", "")
    result.interview_prep = data.get("interview_prep", []) or []
    result.salary_notes = data.get("salary_notes", "")
    weights = {name: w for _, name, w in EVALUATION_DIMENSIONS}
    for dim in data.get("dimensions", []) or []:
        name = dim.get("name", "")
        try:
            score = min(5.0, max(0.0, float(dim.get("score", 0))))
        except (TypeError, ValueError):
            score = 0.0
        result.dimensions.append(DimensionScore(name, score, weights.get(name, 0.05),
                                                dim.get("reasoning", "")))
    total = sum(d.weight for d in result.dimensions)
    if total:
        result.overall_score = round(sum(d.weighted_score for d in result.dimensions) / total, 1)
    result.grade = _score_to_grade(result.overall_score)
    result.recommendation = recommendation_for(result.overall_score)
    return result


def evaluate_text(jd: ExtractedJD, cv_text: str, profile_yaml: str, llm: LLM) -> EvaluationResult:
    """Evaluate with the unified client (used by the agent; CV = the routed track's CV)."""
    result = EvaluationResult(company=jd.company, role=jd.title, url=jd.url,
                              date=datetime.now().strftime("%Y-%m-%d"))
    try:
        data = llm.complete_json(_build_evaluation_prompt(jd, cv_text, profile_yaml))
        return result_from_data(result, data, jd)
    except Exception as e:
        result.error = f"AI evaluation failed: {e}"
        return result


def evaluate_job(
    jd: ExtractedJD,
    paths: ProjectPaths,
    provider: str | None = None,
    api_key: str | None = None,
) -> EvaluationResult:
    """
    Evaluate a job description against the candidate's CV and profile.

    Calls an LLM (Anthropic or OpenAI) to produce a structured evaluation.
    """
    result = EvaluationResult(
        company=jd.company,
        role=jd.title,
        url=jd.url,
        date=datetime.now().strftime("%Y-%m-%d"),
    )

    if not jd.full_text:
        result.error = "No job description text to evaluate"
        return result

    if not paths.cv.exists():
        result.error = "cv.md not found — create it with your CV in markdown"
        console.print(f"[red]{result.error}[/red]")
        return result

    cv_text = paths.cv.read_text(encoding="utf-8")
    profile_yaml = ""
    if paths.profile.exists():
        profile_yaml = paths.profile.read_text(encoding="utf-8")

    prompt = _build_evaluation_prompt(jd, cv_text, profile_yaml)

    detected_provider, detected_key, detected_model = _get_ai_provider()
    use_provider = provider or detected_provider
    use_key = api_key or detected_key

    if not use_provider or (use_provider not in ("ollama",) and not use_key):
        result.error = (
            "No AI provider configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "or OLLAMA_MODEL in your environment."
        )
        console.print(f"[red]{result.error}[/red]")
        return result

    console.print(f"[dim]Evaluating with {use_provider} ({detected_model})...[/dim]")

    try:
        if use_provider == "anthropic":
            raw_response = _call_anthropic(prompt, use_key)
        elif use_provider == "openai":
            raw_response = _call_openai(prompt, use_key, detected_model)
        elif use_provider == "ollama":
            raw_response = _call_ollama(prompt, detected_model)
        else:
            result.error = f"Unknown provider: {use_provider}"
            return result

        data = _parse_ai_response(raw_response)
        result_from_data(result, data, jd)

    except json.JSONDecodeError as e:
        result.error = f"Failed to parse AI response: {e}"
        console.print(f"[red]{result.error}[/red]")
    except Exception as e:
        result.error = f"AI evaluation failed: {e}"
        console.print(f"[red]{result.error}[/red]")

    return result


def display_evaluation(result: EvaluationResult) -> None:
    """Display evaluation results in a rich formatted output."""
    if result.error:
        console.print(f"\n[red]Evaluation error: {result.error}[/red]")
        return

    console.print(f"\n[bold]{'=' * 60}[/bold]")
    console.print(f"[bold cyan]{result.company}[/bold cyan] — [bold]{result.role}[/bold]")
    console.print(f"[dim]{result.url}[/dim]")
    console.print(f"Archetype: [yellow]{result.archetype}[/yellow]")
    console.print(f"[bold]{'=' * 60}[/bold]")

    grade_color = "green" if result.overall_score >= 4.0 else "yellow" if result.overall_score >= 3.0 else "red"
    console.print(
        f"\n[bold {grade_color}]Overall: {result.score_display} (Grade {result.grade})[/bold {grade_color}]"
    )

    rec_colors = {"apply": "green", "consider": "yellow", "skip": "red"}
    rec_color = rec_colors.get(result.recommendation, "white")
    console.print(f"Recommendation: [{rec_color}]{result.recommendation.upper()}[/{rec_color}]")

    console.print(f"\n[italic]{result.summary}[/italic]")

    # Dimension scores table
    dim_table = Table(show_header=True, header_style="bold", title="Dimension Scores")
    dim_table.add_column("Dimension")
    dim_table.add_column("Score", justify="center")
    dim_table.add_column("Weight", justify="center")
    dim_table.add_column("Reasoning")

    for d in result.dimensions:
        score_color = "green" if d.score >= 4.0 else "yellow" if d.score >= 3.0 else "red"
        dim_table.add_row(
            d.name,
            f"[{score_color}]{d.score:.1f}/5[/{score_color}]",
            f"{d.weight:.0%}",
            d.reasoning[:80] + ("..." if len(d.reasoning) > 80 else ""),
        )
    console.print(dim_table)

    if result.strengths:
        console.print("\n[bold green]Strengths:[/bold green]")
        for s in result.strengths:
            console.print(f"  [green]+[/green] {s}")

    if result.gaps:
        console.print("\n[bold red]Gaps:[/bold red]")
        for g in result.gaps:
            console.print(f"  [red]-[/red] {g}")

    if result.tailored_pitch:
        console.print(f"\n[bold]Tailored Pitch:[/bold]\n{result.tailored_pitch}")

    if result.interview_prep:
        console.print("\n[bold]Interview Prep (STAR stories):[/bold]")
        for i, topic in enumerate(result.interview_prep, 1):
            console.print(f"  {i}. {topic}")

    if result.salary_notes:
        console.print(f"\n[bold]Compensation:[/bold] {result.salary_notes}")

    console.print()


def generate_report_markdown(result: EvaluationResult, report_num: int) -> str:
    """Generate a markdown evaluation report."""
    lines = [
        f"# Evaluation Report #{report_num:03d}",
        f"",
        f"**Company:** {result.company}",
        f"**Role:** {result.role}",
        f"**URL:** {result.url}",
        f"**Date:** {result.date}",
        f"**Archetype:** {result.archetype}",
        f"**Overall Score:** {result.score_display} (Grade {result.grade})",
        f"**Recommendation:** {result.recommendation.upper()}",
        f"",
        f"## Summary",
        f"",
        f"{result.summary}",
        f"",
        f"## Dimension Scores",
        f"",
        f"| Dimension | Score | Weight | Reasoning |",
        f"| --- | --- | --- | --- |",
    ]

    for d in result.dimensions:
        lines.append(f"| {d.name} | {d.score:.1f}/5 | {d.weight:.0%} | {d.reasoning} |")

    lines.extend([
        f"",
        f"## Strengths",
        f"",
    ])
    for s in result.strengths:
        lines.append(f"- {s}")

    lines.extend([
        f"",
        f"## Gaps",
        f"",
    ])
    for g in result.gaps:
        lines.append(f"- {g}")

    if result.tailored_pitch:
        lines.extend([
            f"",
            f"## Tailored Pitch",
            f"",
            f"{result.tailored_pitch}",
        ])

    if result.interview_prep:
        lines.extend([
            f"",
            f"## Interview Prep (STAR Stories)",
            f"",
        ])
        for i, topic in enumerate(result.interview_prep, 1):
            lines.append(f"{i}. {topic}")

    if result.salary_notes:
        lines.extend([
            f"",
            f"## Compensation Notes",
            f"",
            f"{result.salary_notes}",
        ])

    return "\n".join(lines) + "\n"
