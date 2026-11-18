"""Cover letters — generated only when a form asks for one (apply.cover_letter=auto)."""

from __future__ import annotations

from pathlib import Path

from .latex_cv import compile_tex, tex_engine
from .llm import LLM, untrusted
from .models import CandidateProfile

PROMPT = """Write a cover letter body (170-230 words, 3 short paragraphs) for this application.
Use ONLY facts from the CV — no invented skills, employers or numbers. Plain text, no
greeting line, no sign-off, no addresses, no placeholders.

Candidate: {name} — {headline}
CV:
{cv}

Job: {title} at {company}
{jd}"""

TEX = r"""\documentclass[a4paper,11pt]{article}
\usepackage[T1]{fontenc}\usepackage[utf8]{inputenc}
\usepackage[left=1in,right=1in,top=1in,bottom=1in]{geometry}
\pagestyle{empty}\setlength{\parindent}{0pt}\setlength{\parskip}{8pt}
\begin{document}
{\large\bfseries %(name)s}\\ %(contact)s

Dear %(company)s Hiring Team,

%(body)s

Sincerely,\\ %(name)s
\end{document}
"""


def _tex_escape(s: str) -> str:
    rep = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
           "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\^{}"}
    return "".join(rep.get(c, c) for c in s)


def write_cover_letter(llm: LLM, profile: CandidateProfile, cv_text: str, title: str,
                       company: str, jd_text: str) -> str:
    return llm.complete(PROMPT.format(name=profile.full_name, headline=profile.headline,
                                      cv=cv_text[:7000], title=title, company=company,
                                      jd=untrusted(jd_text, 5000)), max_tokens=800).strip()


def cover_letter_pdf(text: str, profile: CandidateProfile, company: str, out: Path) -> Path:
    """PDF if TeX is available, else a .txt file (accepted by most ATS uploads)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if tex_engine():
        body = "\n\n".join(_tex_escape(p.strip()) for p in text.split("\n\n") if p.strip())
        contact = _tex_escape(" | ".join(x for x in (profile.email, profile.phone) if x))
        tex = out.with_suffix(".tex")
        tex.write_text(TEX % {"name": _tex_escape(profile.full_name), "contact": contact,
                              "company": _tex_escape(company), "body": body}, encoding="utf-8")
        res = compile_tex(tex, out.with_suffix(".pdf"))
        if res.ok:
            return res.pdf
    txt = out.with_suffix(".txt")
    txt.write_text(f"Dear {company} Hiring Team,\n\n{text}\n\nSincerely,\n{profile.full_name}\n",
                   encoding="utf-8")
    return txt
