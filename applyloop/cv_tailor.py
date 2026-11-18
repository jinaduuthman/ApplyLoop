"""AI-powered CV tailoring — adapts CV per job description."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from rich.console import Console

from .config import ProjectPaths
from .evaluator import _call_anthropic, _call_ollama, _call_openai, _get_ai_provider, EvaluationResult
from .jd_extractor import ExtractedJD

console = Console()


def _build_tailor_prompt(
    cv_text: str,
    jd: ExtractedJD,
    evaluation: EvaluationResult | None = None,
    template_html: str = "",
) -> str:
    """Build prompt for CV tailoring."""
    eval_context = ""
    if evaluation:
        eval_context = f"""
## Evaluation Context
- Strengths to emphasize: {', '.join(evaluation.strengths)}
- Gaps to address: {', '.join(evaluation.gaps)}
- Tailored pitch: {evaluation.tailored_pitch}
- Archetype: {evaluation.archetype}
"""

    return f"""You are an expert ATS resume writer. Tailor the candidate's CV for this specific job.

## Original CV (Markdown)
{cv_text}

## Target Job
**Company:** {jd.company}
**Role:** {jd.title}
**Location:** {jd.location}

### Job Description
{jd.full_text[:6000]}
{eval_context}

## Instructions

Produce a tailored version of the CV that:
1. Reorders bullet points to lead with the most relevant experience
2. Incorporates keywords from the JD naturally (not keyword stuffing)
3. Quantifies achievements where possible
4. Adjusts the professional summary/headline to match the role
5. Keeps ALL truthful information — never invent experience
6. Maintains ATS-friendly formatting (no tables, columns, or graphics)

Return the tailored CV in clean markdown format. Do NOT add any explanation or commentary, just the CV content."""


def tailor_cv(
    paths: ProjectPaths,
    jd: ExtractedJD,
    evaluation: EvaluationResult | None = None,
    output_path: Path | None = None,
) -> Path | None:
    """
    Generate a tailored CV for a specific job description.

    Returns the path to the tailored CV markdown file, or None on failure.
    """
    if not paths.cv.exists():
        console.print("[red]cv.md not found — create it first[/red]")
        return None

    cv_text = paths.cv.read_text(encoding="utf-8")

    template_html = ""
    if paths.cv_template.exists():
        template_html = paths.cv_template.read_text(encoding="utf-8")

    prompt = _build_tailor_prompt(cv_text, jd, evaluation, template_html)

    provider, api_key, model = _get_ai_provider()
    if not provider or (provider not in ("ollama",) and not api_key):
        console.print(
            "[red]No AI provider configured. "
            "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OLLAMA_MODEL.[/red]"
        )
        return None

    console.print(f"[dim]Tailoring CV with {provider} ({model})...[/dim]")

    try:
        if provider == "anthropic":
            tailored_md = _call_anthropic(prompt, api_key)
        elif provider == "ollama":
            tailored_md = _call_ollama(prompt, model)
        else:
            tailored_md = _call_openai(prompt, api_key, model)
    except Exception as e:
        console.print(f"[red]CV tailoring failed: {e}[/red]")
        return None

    tailored_md = tailored_md.strip()
    tailored_md = re.sub(r"^```(?:markdown)?\s*", "", tailored_md)
    tailored_md = re.sub(r"\s*```$", "", tailored_md)

    slug = re.sub(r"[^a-z0-9]+", "-", jd.company.lower()).strip("-")
    out = output_path or (paths.output_dir / f"cv-tailored-{slug}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tailored_md, encoding="utf-8")

    console.print(f"[green]Tailored CV saved: {out}[/green]")
    return out


def tailor_cv_to_html(
    tailored_md_path: Path,
    paths: ProjectPaths,
) -> Path | None:
    """
    Convert a tailored markdown CV into HTML using the template.

    Returns path to the HTML file, or None if no template available.
    """
    if not paths.cv_template.exists():
        console.print("[yellow]No cv-template.html found, skipping HTML conversion[/yellow]")
        return None

    template = paths.cv_template.read_text(encoding="utf-8")
    cv_md = tailored_md_path.read_text(encoding="utf-8")

    lines = cv_md.split("\n")
    html_parts: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            html_parts.append(f"<h1>{stripped[2:]}</h1>")
        elif stripped.startswith("## "):
            html_parts.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("### "):
            html_parts.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith("- "):
            html_parts.append(f"<li>{stripped[2:]}</li>")
        elif stripped.startswith("**") and stripped.endswith("**"):
            html_parts.append(f"<p><strong>{stripped[2:-2]}</strong></p>")
        elif stripped:
            bold_converted = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", stripped)
            italic_converted = re.sub(r"\*(.+?)\*", r"<em>\1</em>", bold_converted)
            html_parts.append(f"<p>{italic_converted}</p>")

    body_html = "\n".join(html_parts)

    if "{{BODY}}" in template:
        final_html = template.replace("{{BODY}}", body_html)
    elif "<body>" in template.lower():
        final_html = re.sub(
            r"(<body[^>]*>)(.*?)(</body>)",
            rf"\1\n{body_html}\n\3",
            template,
            flags=re.DOTALL | re.IGNORECASE,
        )
    else:
        final_html = f"<html><body>\n{body_html}\n</body></html>"

    out = tailored_md_path.with_suffix(".html")
    out.write_text(final_html, encoding="utf-8")
    console.print(f"[green]HTML CV saved: {out}[/green]")
    return out
