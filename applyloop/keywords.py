"""JD-to-CV keyword gap analysis — find missing skills and keywords."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import ProjectPaths

console = Console()

STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "must",
    "that", "which", "who", "whom", "this", "these", "those", "it",
    "its", "you", "your", "we", "our", "they", "their", "he", "she",
    "him", "her", "his", "my", "me", "us", "i", "not", "no", "nor",
    "so", "if", "then", "than", "also", "just", "about", "up", "out",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "only", "same", "into", "over", "after", "before",
    "between", "through", "during", "above", "below", "any", "here",
    "there", "when", "where", "how", "what", "why", "very", "well",
    "work", "working", "experience", "team", "role", "including",
    "ability", "strong", "etc", "e.g", "i.e", "new", "use", "using",
}

TECH_COMPOUND_TERMS = {
    "machine learning", "deep learning", "natural language processing",
    "computer vision", "data science", "data engineering", "full stack",
    "front end", "back end", "ci cd", "devops", "mlops", "data ops",
    "product management", "project management", "version control",
    "test driven", "object oriented", "event driven", "micro services",
    "real time", "open source",
}


def _extract_words(text: str) -> list[str]:
    """Extract meaningful words from text, lowercased, no punctuation."""
    text = text.lower()
    text = re.sub(r"[^\w\s/+#.-]", " ", text)
    words = text.split()
    return [w for w in words if w not in STOP_WORDS and len(w) > 1]


def _extract_compound_terms(text: str) -> list[str]:
    """Extract known compound technical terms."""
    text_lower = text.lower()
    found = []
    for term in TECH_COMPOUND_TERMS:
        if term in text_lower:
            found.append(term)
    return found


def _extract_tech_keywords(text: str) -> set[str]:
    """Extract technology-specific keywords (languages, frameworks, tools)."""
    patterns = [
        r"\b(python|java|javascript|typescript|go|rust|ruby|scala|kotlin|swift|c\+\+|c#)\b",
        r"\b(react|angular|vue|svelte|next\.?js|nuxt|django|flask|fastapi|spring|rails)\b",
        r"\b(aws|gcp|azure|docker|kubernetes|terraform|ansible|jenkins|github\s*actions)\b",
        r"\b(postgresql|mysql|mongodb|redis|elasticsearch|dynamodb|cassandra|sqlite)\b",
        r"\b(pytorch|tensorflow|keras|scikit-learn|pandas|numpy|spark|hadoop|airflow)\b",
        r"\b(graphql|rest|grpc|kafka|rabbitmq|sqs|sns|websocket)\b",
        r"\b(linux|git|bash|sql|html|css|sass|webpack|vite|node\.?js)\b",
    ]
    keywords = set()
    text_lower = text.lower()
    for pattern in patterns:
        for m in re.finditer(pattern, text_lower, re.IGNORECASE):
            keywords.add(m.group(0).strip())
    return keywords


def analyze_keywords(
    jd_text: str,
    cv_text: str,
) -> dict:
    """
    Analyze keyword gaps between a job description and CV.

    Returns dict with:
    - jd_keywords: keywords found in the JD
    - cv_keywords: keywords found in the CV
    - missing: keywords in JD but not in CV
    - matching: keywords found in both
    - match_percentage: how many JD keywords are in CV
    - compound_missing: compound technical terms missing from CV
    """
    jd_words = _extract_words(jd_text)
    cv_words = _extract_words(cv_text)

    jd_tech = _extract_tech_keywords(jd_text)
    cv_tech = _extract_tech_keywords(cv_text)

    jd_compounds = set(_extract_compound_terms(jd_text))
    cv_compounds = set(_extract_compound_terms(cv_text))

    jd_counter = Counter(jd_words)
    cv_word_set = set(cv_words)

    # Focus on words that appear multiple times in JD (more important)
    important_jd_words = {w for w, c in jd_counter.items() if c >= 2}
    # Also include all tech keywords regardless of frequency
    jd_keywords = important_jd_words | jd_tech | jd_compounds
    cv_keywords = cv_word_set | cv_tech | cv_compounds

    matching = jd_keywords & cv_keywords
    missing = jd_keywords - cv_keywords

    match_pct = (len(matching) / len(jd_keywords) * 100) if jd_keywords else 0
    compound_missing = jd_compounds - cv_compounds

    return {
        "jd_keywords": sorted(jd_keywords),
        "cv_keywords": sorted(cv_keywords),
        "missing": sorted(missing),
        "matching": sorted(matching),
        "match_percentage": match_pct,
        "compound_missing": sorted(compound_missing),
        "tech_in_jd": sorted(jd_tech),
        "tech_in_cv": sorted(cv_tech),
        "tech_missing": sorted(jd_tech - cv_tech),
        "tech_match": sorted(jd_tech & cv_tech),
    }


def run_keyword_analysis(
    paths: ProjectPaths,
    jd_path: str | Path | None = None,
    jd_text: str | None = None,
) -> dict:
    """
    Run keyword gap analysis between a JD and the user's CV.

    Provide either jd_path (file) or jd_text (raw text).
    """
    if not paths.cv.exists():
        console.print("[red]cv.md not found. Create it first.[/red]")
        return {}

    cv_text = paths.cv.read_text(encoding="utf-8")

    if jd_path:
        jd_content = Path(jd_path).read_text(encoding="utf-8")
    elif jd_text:
        jd_content = jd_text
    else:
        console.print("[red]Provide either a JD file path or JD text.[/red]")
        return {}

    result = analyze_keywords(jd_content, cv_text)

    # -- Display results
    console.print(f"\n[bold]Keyword Analysis[/bold]")
    console.print(
        f"Match rate: [{'green' if result['match_percentage'] > 70 else 'yellow' if result['match_percentage'] > 50 else 'red'}]"
        f"{result['match_percentage']:.0f}%[/]"
    )

    # Tech keywords
    if result["tech_in_jd"]:
        console.print(f"\n[bold]Tech Stack Required:[/bold]")

        tech_table = Table(show_header=True, header_style="bold")
        tech_table.add_column("Technology")
        tech_table.add_column("In CV?")

        for tech in sorted(result["tech_in_jd"]):
            in_cv = tech in result["tech_in_cv"]
            status = "[green]Yes[/green]" if in_cv else "[red]Missing[/red]"
            tech_table.add_row(tech, status)

        console.print(tech_table)

    # Missing keywords
    if result["missing"]:
        console.print(f"\n[bold yellow]Keywords to add to CV ({len(result['missing'])}):[/bold yellow]")
        # Group into rows of 5
        missing = result["missing"]
        for i in range(0, len(missing), 5):
            chunk = missing[i : i + 5]
            console.print("  " + ", ".join(chunk))

    # Compound terms missing
    if result["compound_missing"]:
        console.print(f"\n[bold yellow]Missing compound terms:[/bold yellow]")
        for term in result["compound_missing"]:
            console.print(f"  - {term}")

    # Matching keywords (summary)
    console.print(f"\n[green]Matching: {len(result['matching'])} keywords in common[/green]")

    return result
