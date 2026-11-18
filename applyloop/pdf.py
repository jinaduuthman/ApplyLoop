"""HTML → PDF generation via Playwright with ATS text normalization."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()

# Unicode replacements for ATS compatibility
ATS_REPLACEMENTS: list[tuple[str, str, str]] = [
    ("\u2014", "-", "em-dash"),
    ("\u2013", "-", "en-dash"),
    ("\u2026", "...", "ellipsis"),
    ("\u00A0", " ", "nbsp"),
]

ATS_PATTERN_REPLACEMENTS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"[\u201C\u201D\u201E\u201F]"), '"', "smart-double-quote"),
    (re.compile(r"[\u2018\u2019\u201A\u201B]"), "'", "smart-single-quote"),
    (re.compile(r"[\u200B\u200C\u200D\u2060\uFEFF]"), "", "zero-width"),
]


def normalize_text_for_ats(html: str) -> tuple[str, dict[str, int]]:
    """
    Normalize problematic Unicode in body text for ATS parsers.

    Preserves CSS, JS, tag attributes, and URLs — only touches visible text nodes.
    Returns (normalized_html, {replacement_type: count}).
    """
    replacements: dict[str, int] = {}

    masks: list[str] = []
    def mask_block(m: re.Match) -> str:
        token = f"\x00MASK{len(masks)}\x00"
        masks.append(m.group(0))
        return token

    masked = re.sub(
        r"<(style|script)\b[^>]*>[\s\S]*?</\1>",
        mask_block,
        html,
        flags=re.IGNORECASE,
    )

    def sanitize_text(text: str) -> str:
        if not text:
            return text
        result = text
        for char, replacement, label in ATS_REPLACEMENTS:
            count = result.count(char)
            if count:
                replacements[label] = replacements.get(label, 0) + count
                result = result.replace(char, replacement)
        for pattern, replacement, label in ATS_PATTERN_REPLACEMENTS:
            matches = pattern.findall(result)
            if matches:
                replacements[label] = replacements.get(label, 0) + len(matches)
                result = pattern.sub(replacement, result)
        return result

    out_parts: list[str] = []
    i = 0
    while i < len(masked):
        lt = masked.find("<", i)
        if lt == -1:
            out_parts.append(sanitize_text(masked[i:]))
            break
        out_parts.append(sanitize_text(masked[i:lt]))
        gt = masked.find(">", lt)
        if gt == -1:
            out_parts.append(masked[lt:])
            break
        out_parts.append(masked[lt : gt + 1])
        i = gt + 1

    result = "".join(out_parts)

    def unmask(m: re.Match) -> str:
        return masks[int(m.group(1))]

    result = re.sub(r"\x00MASK(\d+)\x00", unmask, result)
    return result, replacements


def generate_pdf(
    input_path: str | Path,
    output_path: str | Path,
    page_format: str = "a4",
    fonts_dir: Path | None = None,
) -> dict:
    """
    Generate a PDF from an HTML file using Playwright.

    Returns dict with {output_path, page_count, size_bytes}.
    """
    from playwright.sync_api import sync_playwright

    input_path = Path(input_path).resolve()
    output_path = Path(output_path).resolve()

    valid_formats = ("a4", "letter")
    if page_format.lower() not in valid_formats:
        raise ValueError(f"Invalid format '{page_format}'. Use: {', '.join(valid_formats)}")

    console.print(f"[dim]Input:  {input_path}[/dim]")
    console.print(f"[dim]Output: {output_path}[/dim]")
    console.print(f"[dim]Format: {page_format.upper()}[/dim]")

    html = input_path.read_text(encoding="utf-8")

    if fonts_dir and fonts_dir.exists():
        fonts_uri = fonts_dir.resolve().as_uri()
        html = re.sub(
            r"url\(['\"]?\./fonts/",
            f"url('{fonts_uri}/",
            html,
        )

    html, ats_replacements = normalize_text_for_ats(html)
    total = sum(ats_replacements.values())
    if total > 0:
        breakdown = ", ".join(f"{k}={v}" for k, v in ats_replacements.items())
        console.print(f"[yellow]ATS normalization: {total} replacements ({breakdown})[/yellow]")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.set_content(html, wait_until="networkidle")
        page.evaluate("() => document.fonts.ready")

        pdf_bytes = page.pdf(
            format=page_format,
            print_background=True,
            margin={
                "top": "0.6in",
                "right": "0.6in",
                "bottom": "0.6in",
                "left": "0.6in",
            },
        )

        browser.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf_bytes)

    page_count = len(re.findall(rb"/Type\s*/Page[^s]", pdf_bytes))
    size_bytes = len(pdf_bytes)

    console.print(f"[green]PDF generated: {output_path}[/green]")
    console.print(f"[dim]Pages: {page_count} | Size: {size_bytes / 1024:.1f} KB[/dim]")

    return {
        "output_path": str(output_path),
        "page_count": page_count,
        "size_bytes": size_bytes,
    }
