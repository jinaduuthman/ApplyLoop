"""Auto-apply engine — fills and submits job applications.

Supports two backends:
  - **Stagehand** (AI-powered): Uses natural language to fill forms on any
    ATS. Enable with STAGEHAND_ENABLED=true in .env.
  - **Playwright** (selector-based): Uses CSS selectors for Greenhouse,
    Lever, and generic forms. Used as the default fallback.

Default mode is human-in-the-loop: fills the form, then pauses for review.
Pass auto_submit=True to skip the pause and submit automatically.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from .config import ProjectPaths, load_profile
from .evaluator import _call_anthropic, _call_ollama, _call_openai, _get_ai_provider
from .jd_extractor import ExtractedJD, _detect_ats
from .models import CandidateProfile
from .stagehand_browser import is_stagehand_enabled

console = Console()


@dataclass
class ApplicationResult:
    url: str
    company: str
    role: str
    status: str  # "submitted", "review_paused", "failed", "skipped"
    platform: str = ""
    error: str = ""
    timestamp: str = ""
    fields_filled: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "company": self.company,
            "role": self.role,
            "status": self.status,
            "platform": self.platform,
            "error": self.error,
            "timestamp": self.timestamp,
            "fields_filled": self.fields_filled,
        }


def _generate_cover_letter(
    profile: CandidateProfile, jd: ExtractedJD, cv_text: str
) -> str:
    """Generate a short, targeted cover letter using AI."""
    provider, api_key, model = _get_ai_provider()
    if not provider or (provider != "ollama" and not api_key):
        return ""

    prompt = f"""Write a concise, compelling cover letter (150-200 words) for this application.

Candidate: {profile.full_name}
Headline: {profile.headline}
Superpowers: {', '.join(profile.superpowers)}

Job: {jd.title} at {jd.company}
Description excerpt: {jd.full_text[:2000]}

CV excerpt: {cv_text[:2000]}

Requirements:
- Professional but personable tone
- Lead with the most relevant qualification
- Reference a specific company detail from the JD
- Close with enthusiasm and a call to action
- Do NOT include addresses or dates — just the letter body
- Return ONLY the letter text, no commentary"""

    try:
        if provider == "anthropic":
            return _call_anthropic(prompt, api_key)
        if provider == "ollama":
            return _call_ollama(prompt, model)
        return _call_openai(prompt, api_key, model)
    except Exception as e:
        console.print(f"[yellow]Cover letter generation failed: {e}[/yellow]")
        return ""


def _answer_custom_question(question_text: str, profile: CandidateProfile, jd: ExtractedJD) -> str:
    """Use AI to answer a custom application question."""
    provider, api_key, model = _get_ai_provider()
    if not provider or (provider != "ollama" and not api_key):
        return ""

    prompt = f"""Answer this job application question concisely and professionally.

Question: {question_text}

Candidate info:
- Name: {profile.full_name}
- Location: {profile.city}, {profile.country}
- Target compensation: {profile.target_comp}
- Headline: {profile.headline}
- Superpowers: {', '.join(profile.superpowers)}

Job: {jd.title} at {jd.company}

Answer in 1-3 sentences. Be specific and honest. Return ONLY the answer."""

    try:
        if provider == "anthropic":
            return _call_anthropic(prompt, api_key).strip()
        if provider == "ollama":
            return _call_ollama(prompt, model).strip()
        return _call_openai(prompt, api_key, model).strip()
    except Exception:
        return ""


def _fill_text_field(page, selector: str, value: str) -> bool:
    """Safely fill a text field if it exists."""
    try:
        el = page.query_selector(selector)
        if el and el.is_visible():
            el.fill(value)
            return True
    except Exception:
        pass
    return False


def _upload_file(page, selector: str, file_path: str) -> bool:
    """Upload a file to a file input."""
    try:
        el = page.query_selector(selector)
        if el:
            el.set_input_files(file_path)
            return True
    except Exception:
        pass
    return False


def _apply_greenhouse(
    page,
    profile: CandidateProfile,
    resume_path: Path | None,
    cover_letter: str,
    jd: ExtractedJD,
) -> list[str]:
    """Fill a Greenhouse application form."""
    filled: list[str] = []

    field_map = {
        "#first_name": profile.full_name.split()[0] if profile.full_name else "",
        "#last_name": " ".join(profile.full_name.split()[1:]) if profile.full_name else "",
        "#email": profile.email,
        "#phone": profile.phone,
        "input[name*='first_name']": profile.full_name.split()[0] if profile.full_name else "",
        "input[name*='last_name']": " ".join(profile.full_name.split()[1:]) if profile.full_name else "",
        "input[name*='email']": profile.email,
        "input[name*='phone']": profile.phone,
    }

    for selector, value in field_map.items():
        if value and _fill_text_field(page, selector, value):
            filled.append(selector)

    linkedin_selectors = [
        "input[name*='linkedin']",
        "input[placeholder*='LinkedIn']",
        "input[id*='linkedin']",
        "input[autocomplete*='linkedin']",
    ]
    for sel in linkedin_selectors:
        if profile.linkedin and _fill_text_field(page, sel, profile.linkedin):
            filled.append("linkedin")
            break

    website_selectors = [
        "input[name*='website']",
        "input[name*='portfolio']",
        "input[placeholder*='Website']",
    ]
    for sel in website_selectors:
        url = profile.portfolio_url or profile.github
        if url and _fill_text_field(page, sel, url):
            filled.append("website")
            break

    if resume_path and resume_path.exists():
        resume_selectors = [
            "input[type='file'][name*='resume']",
            "input[type='file'][id*='resume']",
            "input[type='file']:first-of-type",
        ]
        for sel in resume_selectors:
            if _upload_file(page, sel, str(resume_path)):
                filled.append("resume")
                break

    if cover_letter:
        cover_selectors = [
            "textarea[name*='cover']",
            "textarea[id*='cover']",
            "textarea[placeholder*='Cover']",
            "input[type='file'][name*='cover']",
        ]
        for sel in cover_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    tag = el.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "textarea":
                        el.fill(cover_letter)
                        filled.append("cover_letter")
                        break
            except Exception:
                continue

    _handle_custom_questions(page, profile, jd)

    return filled


def _apply_lever(
    page,
    profile: CandidateProfile,
    resume_path: Path | None,
    cover_letter: str,
    jd: ExtractedJD,
) -> list[str]:
    """Fill a Lever application form."""
    filled: list[str] = []

    field_map = {
        "input[name='name']": profile.full_name,
        "input[name='email']": profile.email,
        "input[name='phone']": profile.phone,
        "input[name='org']": "",
        "input[name='urls[LinkedIn]']": profile.linkedin,
        "input[name='urls[Portfolio]']": profile.portfolio_url,
        "input[name='urls[GitHub]']": profile.github,
        "input[name='urls[Twitter]']": profile.twitter,
    }

    for selector, value in field_map.items():
        if value and _fill_text_field(page, selector, value):
            filled.append(selector.split("'")[1] if "'" in selector else selector)

    if resume_path and resume_path.exists():
        resume_sel = "input[type='file'][name='resume']"
        if _upload_file(page, resume_sel, str(resume_path)):
            filled.append("resume")
        else:
            fallback_sel = "input[type='file']"
            if _upload_file(page, fallback_sel, str(resume_path)):
                filled.append("resume")

    if cover_letter:
        cover_sel = "textarea[name='comments']"
        if _fill_text_field(page, cover_sel, cover_letter):
            filled.append("cover_letter")

    _handle_custom_questions(page, profile, jd)

    return filled


def _apply_generic(
    page,
    profile: CandidateProfile,
    resume_path: Path | None,
    cover_letter: str,
    jd: ExtractedJD,
) -> list[str]:
    """Fill a generic job application form using heuristic field detection."""
    filled: list[str] = []

    name_patterns = [
        "input[name*='name' i]",
        "input[placeholder*='name' i]",
        "input[id*='name' i]",
        "input[autocomplete='name']",
    ]
    for sel in name_patterns:
        if _fill_text_field(page, sel, profile.full_name):
            filled.append("name")
            break

    first_last = [
        ("input[name*='first' i]", profile.full_name.split()[0] if profile.full_name else ""),
        ("input[name*='last' i]", " ".join(profile.full_name.split()[1:]) if profile.full_name else ""),
    ]
    for sel, val in first_last:
        if val and _fill_text_field(page, sel, val):
            filled.append(sel)

    email_patterns = [
        "input[type='email']",
        "input[name*='email' i]",
        "input[placeholder*='email' i]",
        "input[autocomplete='email']",
    ]
    for sel in email_patterns:
        if _fill_text_field(page, sel, profile.email):
            filled.append("email")
            break

    phone_patterns = [
        "input[type='tel']",
        "input[name*='phone' i]",
        "input[placeholder*='phone' i]",
        "input[autocomplete='tel']",
    ]
    for sel in phone_patterns:
        if profile.phone and _fill_text_field(page, sel, profile.phone):
            filled.append("phone")
            break

    linkedin_patterns = [
        "input[name*='linkedin' i]",
        "input[placeholder*='LinkedIn' i]",
        "input[id*='linkedin' i]",
    ]
    for sel in linkedin_patterns:
        if profile.linkedin and _fill_text_field(page, sel, profile.linkedin):
            filled.append("linkedin")
            break

    if resume_path and resume_path.exists():
        file_inputs = page.query_selector_all("input[type='file']")
        for fi in file_inputs:
            try:
                label_text = ""
                fi_id = fi.get_attribute("id") or ""
                fi_name = fi.get_attribute("name") or ""
                if fi_id:
                    label = page.query_selector(f"label[for='{fi_id}']")
                    if label:
                        label_text = label.inner_text().lower()

                if any(kw in (label_text + fi_name.lower() + fi_id.lower())
                       for kw in ["resume", "cv", "upload"]):
                    fi.set_input_files(str(resume_path))
                    filled.append("resume")
                    break
            except Exception:
                continue

    if cover_letter:
        cover_patterns = [
            "textarea[name*='cover' i]",
            "textarea[placeholder*='cover' i]",
            "textarea[name*='letter' i]",
            "textarea:not([name*='note'])",
        ]
        for sel in cover_patterns:
            if _fill_text_field(page, sel, cover_letter):
                filled.append("cover_letter")
                break

    _handle_custom_questions(page, profile, jd)

    return filled


def _handle_custom_questions(
    page, profile: CandidateProfile, jd: ExtractedJD
) -> None:
    """Attempt to answer custom application questions using AI."""
    try:
        question_containers = page.query_selector_all(
            "div[class*='custom-question'], "
            "div[class*='question'], "
            "fieldset, "
            ".field"
        )

        for container in question_containers:
            label = container.query_selector("label, legend, .label, h3, h4")
            if not label:
                continue

            question_text = label.inner_text().strip()
            if len(question_text) < 10 or len(question_text) > 500:
                continue

            text_input = container.query_selector(
                "textarea, input[type='text']:not([name*='name']):not([name*='email'])"
            )
            if not text_input:
                continue

            current_val = text_input.evaluate("el => el.value")
            if current_val:
                continue

            known_answers = _match_known_answer(question_text, profile)
            if known_answers:
                text_input.fill(known_answers)
            else:
                answer = _answer_custom_question(question_text, profile, jd)
                if answer:
                    text_input.fill(answer)
    except Exception:
        pass


def _match_known_answer(question: str, profile: CandidateProfile) -> str:
    """Answer from profile.yml + config/answers.yml only (whole-word matching, no guesses).

    Previously this used substring checks ("city" matched "ethnicity", "rate" matched
    "separate") and hard-coded work-authorization answers.
    """
    from .answers import AnswerBank
    from .config import ProjectPaths

    bank = AnswerBank.load(ProjectPaths().config_dir / "answers.yml", profile)
    return bank.resolve(question, allow_ai=False).value


def _find_submit_button(page):
    """Locate the submit button on the form."""
    selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Submit')",
        "button:has-text('Apply')",
        "button:has-text('Send')",
        "a:has-text('Submit Application')",
    ]
    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                return btn
        except Exception:
            continue
    return None


def _apply_with_stagehand(
    paths: ProjectPaths,
    url: str,
    profile: CandidateProfile,
    jd: ExtractedJD | None,
    resume_path: Path | None,
    cover_letter: str,
    auto_submit: bool,
) -> ApplicationResult:
    """Fill and submit an application using Stagehand AI — works on any ATS."""
    from .stagehand_browser import FORM_FIELDS_SCHEMA, StagehandBrowser

    result = ApplicationResult(
        url=url,
        company=jd.company if jd else "",
        role=jd.title if jd else "",
        platform=_detect_ats(url),
        timestamp=datetime.now().isoformat(),
    )

    try:
        with StagehandBrowser(headless=False) as browser:
            browser.navigate(url)

            browser.act("If there is an 'Apply' or 'Apply Now' button on the page, click it")
            time.sleep(2)

            form_data = browser.extract(
                instruction="Identify all form fields on this application page",
                schema=FORM_FIELDS_SCHEMA,
            )

            fields = form_data.get("fields", []) if isinstance(form_data, dict) else []
            if not fields:
                result.status = "failed"
                result.error = "No form fields detected by Stagehand"
                console.print("[red]No application form fields found[/red]")
                return result

            filled: list[str] = []

            name_parts = profile.full_name.split() if profile.full_name else []
            first_name = name_parts[0] if name_parts else ""
            last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

            field_value_map = {
                "name": profile.full_name,
                "full name": profile.full_name,
                "first name": first_name,
                "last name": last_name,
                "email": profile.email,
                "phone": profile.phone,
                "linkedin": profile.linkedin,
                "github": profile.github,
                "portfolio": profile.portfolio_url,
                "website": profile.portfolio_url or profile.github,
                "location": f"{profile.city}, {profile.country}" if profile.city else profile.country,
                "salary": profile.target_comp,
                "compensation": profile.target_comp,
            }

            for form_field in fields:
                label = form_field.get("label", "").lower()
                field_type = form_field.get("type", "text")

                if field_type == "file":
                    continue

                value = ""
                for key, val in field_value_map.items():
                    if key in label and val:
                        value = val
                        break

                if not value and "cover" in label and cover_letter:
                    value = cover_letter

                if not value:
                    known = _match_known_answer(form_field.get("label", ""), profile)
                    if known:
                        value = known

                if value:
                    try:
                        browser.act(
                            f"Fill the field labeled '{form_field.get('label', '')}' "
                            f"with the value: %value%",
                            variables={"value": value},
                        )
                        filled.append(form_field.get("label", "unknown"))
                    except Exception:
                        pass

            if resume_path and resume_path.exists():
                try:
                    browser.act(
                        f"Upload the file at '{resume_path}' to the resume/CV upload field"
                    )
                    filled.append("resume_upload")
                except Exception:
                    console.print("[yellow]Resume upload via Stagehand not supported — upload manually[/yellow]")

            result.fields_filled = filled

            if not filled:
                result.status = "failed"
                result.error = "Could not fill any form fields"
                console.print("[red]No fields were filled[/red]")
                return result

            console.print(f"\n[green]Filled {len(filled)} fields: {', '.join(filled)}[/green]")

            if auto_submit:
                try:
                    browser.act("Click the submit or apply button to submit this application")
                    time.sleep(3)
                    result.status = "submitted"
                    console.print("[bold green]Application submitted![/bold green]")
                except Exception as e:
                    result.status = "failed"
                    result.error = f"Submit failed: {e}"
                    console.print(f"[red]Submit failed: {e}[/red]")
            else:
                console.print(Panel(
                    "[bold yellow]Form is filled — review it in the browser window.[/bold yellow]\n\n"
                    "Check that all fields are correct, then:\n"
                    "  - Submit manually in the browser, or\n"
                    "  - Press Enter here to auto-submit, or\n"
                    "  - Type 'skip' to skip this application.",
                    title="Human Review",
                    style="yellow",
                ))

                user_input = input("\nPress Enter to submit, or type 'skip': ").strip().lower()

                if user_input == "skip":
                    result.status = "skipped"
                    console.print("[dim]Application skipped[/dim]")
                else:
                    try:
                        browser.act("Click the submit or apply button to submit this application")
                        time.sleep(3)
                        result.status = "submitted"
                        console.print("[bold green]Application submitted![/bold green]")
                    except Exception:
                        result.status = "review_paused"
                        result.error = "Submit failed — submit manually in the browser"
                        console.print("[yellow]Submit failed — please submit manually[/yellow]")
                        input("Press Enter after you've submitted manually...")
                        result.status = "submitted"

    except Exception as e:
        result.status = "failed"
        result.error = str(e).split("\n")[0]
        console.print(f"[red]Stagehand application failed: {result.error}[/red]")

    return result


def apply_to_job(
    paths: ProjectPaths,
    url: str,
    jd: ExtractedJD | None = None,
    resume_path: Path | None = None,
    auto_submit: bool = False,
    generate_cover: bool = True,
) -> ApplicationResult:
    """
    Apply to a job posting by filling and submitting the application form.

    When STAGEHAND_ENABLED=true, uses AI to fill forms on any ATS.
    Otherwise falls back to Playwright CSS selectors.

    By default, pauses after filling the form for human review.
    Set auto_submit=True to submit without pause.
    """
    from playwright.sync_api import sync_playwright

    profile = load_profile(paths)
    if not profile or not profile.full_name:
        return ApplicationResult(
            url=url, company="", role="",
            status="failed", error="profile.yml not found or incomplete",
            timestamp=datetime.now().isoformat(),
        )

    if not resume_path or not Path(resume_path).exists():
        # Never guess: the newest PDF in output/ could be someone else's resume.
        return ApplicationResult(
            url=url, company=jd.company if jd else "", role=jd.title if jd else "",
            status="failed", error="No resume given — pass --resume (or use `applyloop hunt`, which picks the right CV)",
            timestamp=datetime.now().isoformat(),
        )

    cover_letter = ""
    if generate_cover and jd and jd.full_text:
        console.print("[dim]Generating cover letter...[/dim]")
        cv_text = ""
        if paths.cv.exists():
            cv_text = paths.cv.read_text(encoding="utf-8")
        cover_letter = _generate_cover_letter(profile, jd, cv_text)
        if cover_letter:
            console.print("[green]Cover letter generated[/green]")

    platform = _detect_ats(url)
    console.print(f"\n[bold]Applying to: {jd.company if jd else url}[/bold]")
    console.print(f"  Platform: {platform}")
    if is_stagehand_enabled():
        console.print(f"  Backend: [bold green]Stagehand AI[/bold green]")
    console.print(f"  Resume: {resume_path or 'none'}")
    console.print(f"  Cover letter: {'yes' if cover_letter else 'no'}")

    if is_stagehand_enabled():
        return _apply_with_stagehand(
            paths, url, profile, jd, resume_path, cover_letter, auto_submit,
        )

    result = ApplicationResult(
        url=url,
        company=jd.company if jd else "",
        role=jd.title if jd else "",
        platform=platform,
        timestamp=datetime.now().isoformat(),
    )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            if not jd:
                title_el = page.query_selector("h1, h2")
                if title_el:
                    result.role = title_el.inner_text().strip()

            apply_btn = page.query_selector(
                "a:has-text('Apply'), "
                "button:has-text('Apply'), "
                "a[href*='apply'], "
                "a[class*='apply']"
            )
            if apply_btn and apply_btn.is_visible():
                apply_btn.click()
                page.wait_for_timeout(2000)

            if platform == "greenhouse":
                filled = _apply_greenhouse(page, profile, resume_path, cover_letter, jd or ExtractedJD(url=url))
            elif platform == "lever":
                filled = _apply_lever(page, profile, resume_path, cover_letter, jd or ExtractedJD(url=url))
            else:
                filled = _apply_generic(page, profile, resume_path, cover_letter, jd or ExtractedJD(url=url))

            result.fields_filled = filled

            if not filled:
                result.status = "failed"
                result.error = "Could not detect or fill any form fields"
                console.print("[red]Could not find application form fields[/red]")
                browser.close()
                return result

            console.print(f"\n[green]Filled {len(filled)} fields: {', '.join(filled)}[/green]")

            if auto_submit:
                submit_btn = _find_submit_button(page)
                if submit_btn:
                    console.print("[bold yellow]Auto-submitting...[/bold yellow]")
                    submit_btn.click()
                    page.wait_for_timeout(3000)
                    result.status = "submitted"
                    console.print("[bold green]Application submitted![/bold green]")
                else:
                    result.status = "failed"
                    result.error = "Submit button not found"
                    console.print("[red]Submit button not found[/red]")
            else:
                console.print(Panel(
                    "[bold yellow]Form is filled — review it in the browser window.[/bold yellow]\n\n"
                    "Check that all fields are correct, then:\n"
                    "  • Submit manually in the browser, or\n"
                    "  • Press Enter here to auto-submit, or\n"
                    "  • Type 'skip' to skip this application.",
                    title="Human Review",
                    style="yellow",
                ))

                user_input = input("\nPress Enter to submit, or type 'skip': ").strip().lower()

                if user_input == "skip":
                    result.status = "skipped"
                    console.print("[dim]Application skipped[/dim]")
                else:
                    submit_btn = _find_submit_button(page)
                    if submit_btn:
                        submit_btn.click()
                        page.wait_for_timeout(3000)
                        result.status = "submitted"
                        console.print("[bold green]Application submitted![/bold green]")
                    else:
                        result.status = "review_paused"
                        result.error = "Submit button not found — submit manually"
                        console.print("[yellow]Submit button not found — please submit manually in the browser[/yellow]")
                        input("Press Enter after you've submitted manually...")
                        result.status = "submitted"

            browser.close()

    except Exception as e:
        result.status = "failed"
        result.error = str(e).split("\n")[0]
        console.print(f"[red]Application failed: {result.error}[/red]")

    return result


def apply_batch(
    paths: ProjectPaths,
    urls: list[str],
    auto_submit: bool = False,
    generate_cover: bool = True,
    cooldown: int = 30,
) -> list[ApplicationResult]:
    """
    Apply to multiple jobs sequentially with cooldown between applications.

    Returns list of ApplicationResults.
    """
    results: list[ApplicationResult] = []

    console.print(f"[bold]Batch apply: {len(urls)} jobs[/bold]")
    console.print(f"  Auto-submit: {'yes' if auto_submit else 'no (human review)'}")
    console.print(f"  Cooldown: {cooldown}s between applications\n")

    for i, url in enumerate(urls, 1):
        console.print(f"\n{'=' * 60}")
        console.print(f"[bold cyan]Application {i}/{len(urls)}[/bold cyan]")
        console.print(f"{'=' * 60}")

        result = apply_to_job(
            paths,
            url=url,
            auto_submit=auto_submit,
            generate_cover=generate_cover,
        )
        results.append(result)

        if i < len(urls) and result.status != "skipped":
            console.print(f"\n[dim]Cooling down {cooldown}s before next application...[/dim]")
            time.sleep(cooldown)

    _print_batch_summary(results)
    return results


def _print_batch_summary(results: list[ApplicationResult]) -> None:
    """Display a summary table of batch application results."""
    from rich.table import Table

    console.print(f"\n{'=' * 60}")
    console.print("[bold]Application Summary[/bold]\n")

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Platform")
    table.add_column("Status")
    table.add_column("Fields")

    for i, r in enumerate(results, 1):
        status_color = {
            "submitted": "green",
            "skipped": "dim",
            "failed": "red",
            "review_paused": "yellow",
        }.get(r.status, "white")

        table.add_row(
            str(i),
            r.company[:25],
            r.role[:30],
            r.platform,
            f"[{status_color}]{r.status.upper()}[/{status_color}]",
            str(len(r.fields_filled)),
        )

    console.print(table)

    submitted = sum(1 for r in results if r.status == "submitted")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")

    console.print(f"\n[bold]Submitted: {submitted}[/bold] | Failed: {failed} | Skipped: {skipped}")


def save_application_log(
    results: list[ApplicationResult], output_path: Path
) -> None:
    """Persist application results to a JSON log."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing: list[dict] = []
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            existing = []

    existing.extend(r.to_dict() for r in results)
    output_path.write_text(
        json.dumps(existing, indent=2), encoding="utf-8"
    )
    console.print(f"[green]Application log saved: {output_path}[/green]")
