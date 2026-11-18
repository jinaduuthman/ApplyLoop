"""Parse, edit and compile LaTeX resumes without disturbing their layout.

Recognized structure (the common "\\header{...}" + itemize resume style, and most
moderncv/Jake's-style variants that use \\section or \\header for headings):
  * summary   — the paragraph block right after a heading containing "Summary"/"Profile"
  * skills    — tabularx/tabular rows `Label: & items \\\\` under a "Skills" heading
  * bullets   — nested `\\item ...` lines under an "Experience" heading, grouped by role

Edits are applied line-by-line to the original source, so preamble, macros and
spacing are untouched — the output renders exactly like the original.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HEADING_RE = re.compile(r"^\s*\\(?:header|section\*?|cvsection)\{(?:\\bf\{)?([^}]*)\}?\}?")
SKILL_ROW_RE = re.compile(r"^(\s*)(.+?):\s*&\s*(.+?)\s*\\\\\s*$")
ROLE_RE = re.compile(r"^\s*\\item\s*\{\\bf\s+(.+?)\}")
BULLET_RE = re.compile(r"^(\s+)\\item\s+(.+?)\s*$")


@dataclass
class Bullet:
    id: str
    line: int
    text: str
    indent: str


@dataclass
class Role:
    id: str
    title: str
    bullets: list[Bullet] = field(default_factory=list)


@dataclass
class SkillRow:
    id: str
    line: int
    label: str
    value: str
    indent: str


@dataclass
class TexCV:
    path: Path
    lines: list[str]
    summary_lines: tuple[int, int] | None = None     # [start, end) line range of summary text
    skills: list[SkillRow] = field(default_factory=list)
    roles: list[Role] = field(default_factory=list)

    # ── parsing ──────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path | str) -> "TexCV":
        p = Path(path)
        return cls.parse(p.read_text(encoding="utf-8"), p)

    @classmethod
    def parse(cls, source: str, path: Path | None = None) -> "TexCV":
        cv = cls(path=path or Path("cv.tex"), lines=source.split("\n"))
        sections: list[tuple[str, int]] = []
        begin = next((i for i, l in enumerate(cv.lines) if "\\begin{document}" in l), 0)
        for i, line in enumerate(cv.lines):
            if i < begin or line.lstrip().startswith("%"):
                continue
            m = HEADING_RE.match(line)
            if m:
                sections.append((m.group(1).strip().lower(), i))
        bounds = [(name, start, sections[k + 1][1] if k + 1 < len(sections) else len(cv.lines))
                  for k, (name, start) in enumerate(sections)]
        for name, start, end in bounds:
            if any(w in name for w in ("summary", "profile", "objective")) and cv.summary_lines is None:
                cv._parse_summary(start + 1, end)
            elif "skill" in name and not cv.skills:
                cv._parse_skills(start + 1, end)
            elif "experience" in name and not cv.roles:
                cv._parse_experience(start + 1, end)
        return cv

    def _parse_summary(self, start: int, end: int) -> None:
        text_idx = [i for i in range(start, end)
                    if self.lines[i].strip() and not self.lines[i].strip().startswith(("%", "\\", "{", "}"))]
        if text_idx:
            self.summary_lines = (text_idx[0], text_idx[-1] + 1)

    def _parse_skills(self, start: int, end: int) -> None:
        for i in range(start, end):
            m = SKILL_ROW_RE.match(self.lines[i])
            if m and not self.lines[i].lstrip().startswith("%"):
                self.skills.append(SkillRow(f"s{len(self.skills)}", i, m.group(2).strip(),
                                            m.group(3).strip(), m.group(1)))

    def _parse_experience(self, start: int, end: int) -> None:
        role: Role | None = None
        for i in range(start, end):
            line = self.lines[i]
            if line.lstrip().startswith("%"):
                continue
            rm = ROLE_RE.match(line)
            if rm:
                role = Role(f"r{len(self.roles)}", rm.group(1).strip())
                self.roles.append(role)
                continue
            bm = BULLET_RE.match(line)
            if bm and role is not None and len(bm.group(1)) >= 2:
                n = sum(len(r.bullets) for r in self.roles)
                role.bullets.append(Bullet(f"b{n}", i, bm.group(2), bm.group(1)))

    # ── views ────────────────────────────────────────────────────────
    @property
    def summary(self) -> str:
        if not self.summary_lines:
            return ""
        a, b = self.summary_lines
        return " ".join(l.strip() for l in self.lines[a:b])

    def bullet(self, bid: str) -> Bullet | None:
        return next((b for r in self.roles for b in r.bullets if b.id == bid), None)

    def outline(self) -> dict:
        return {
            "summary": self.summary,
            "skills": {s.id: f"{s.label}: {s.value}" for s in self.skills},
            "roles": [{"id": r.id, "title": r.title,
                       "bullets": {b.id: b.text for b in r.bullets}} for r in self.roles],
        }

    def plain_text(self) -> str:
        return latex_to_text("\n".join(self.lines))

    # ── editing ──────────────────────────────────────────────────────
    def set_summary(self, text: str) -> None:
        if not self.summary_lines:
            return
        a, b = self.summary_lines
        self.lines[a] = escape_specials(text)
        for i in range(a + 1, b):
            self.lines[i] = "%JP-REMOVED"
        self.summary_lines = (a, a + 1)

    def set_skill(self, sid: str, value: str) -> None:
        row = next((s for s in self.skills if s.id == sid), None)
        if row:
            row.value = escape_specials(value)
            self.lines[row.line] = f"{row.indent}{row.label}: & {row.value} \\\\"

    def set_bullet(self, bid: str, text: str) -> None:
        b = self.bullet(bid)
        if b:
            b.text = escape_specials(text)
            self.lines[b.line] = f"{b.indent}\\item {b.text}"

    def reorder(self, rid: str, order: list[str]) -> bool:
        role = next((r for r in self.roles if r.id == rid), None)
        if not role or sorted(order) != sorted(b.id for b in role.bullets):
            return False
        slots = [b.line for b in role.bullets]
        by_id = {b.id: b for b in role.bullets}
        texts = [self.lines[by_id[i].line] for i in order]
        for slot, text, bid in zip(slots, texts, order):
            self.lines[slot] = text
            by_id[bid].line = slot
        role.bullets = [by_id[i] for i in order]
        return True

    def drop_bullet(self, bid: str) -> None:
        for r in self.roles:
            for b in list(r.bullets):
                if b.id == bid:
                    self.lines[b.line] = "%JP-REMOVED " + self.lines[b.line].strip()
                    r.bullets.remove(b)

    def source(self) -> str:
        return "\n".join(l for l in self.lines if l != "%JP-REMOVED")

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.source(), encoding="utf-8")
        return path


# ── text helpers ─────────────────────────────────────────────────────

def escape_specials(text: str) -> str:
    """Escape bare &, %, # (models often emit '35%'); leave existing LaTeX alone."""
    text = re.sub(r"(?<!\\)([&%#])", r"\\\1", text.strip())
    return text.replace("\n", " ")


def braces_balanced(text: str) -> bool:
    depth = 0
    for i, ch in enumerate(text):
        if ch in "{}" and i > 0 and text[i - 1] == "\\":
            continue
        depth += 1 if ch == "{" else -1 if ch == "}" else 0
        if depth < 0:
            return False
    return depth == 0


def latex_to_text(src: str) -> str:
    s = re.sub(r"(?m)^\s*%.*$", "", src)
    s = re.sub(r"\\[vh]space\*?\{[^}]*\}|\\(begin|end)\{[^}]*\}(\{[^}]*\})?(\[[^\]]*\])?", " ", s)
    s = re.sub(r"\\moveleft[^\\]*|\\hfill|\\\\|\\vrule[^\n]*", " ", s)
    s = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", s)
    s = s.replace("\\%", "%").replace("\\&", "&").replace("\\,", " ").replace("~", " ")
    s = re.sub(r"\$\\times\$", "x", s)
    s = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", s)
    s = re.sub(r"[{}$\\]", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def numbers_in(text: str) -> set[str]:
    t = text.replace("\\%", "%").replace("\\,", "")
    return set(re.findall(r"\d+(?:\.\d+)?", t))


# ── compiling ────────────────────────────────────────────────────────

def tex_engine() -> list[str] | None:
    if shutil.which("tectonic"):
        return ["tectonic", "--keep-logs", "--chatter", "minimal"]
    if shutil.which("latexmk"):
        return ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error"]
    if shutil.which("pdflatex"):
        return ["pdflatex", "-interaction=nonstopmode", "-halt-on-error"]
    return None


@dataclass
class CompileResult:
    ok: bool
    pdf: Path | None
    pages: int
    log_tail: str = ""


def _count_pages(pdf: Path, log: str) -> int:
    m = re.search(r"Output written on .*?\((\d+) pages?", log, re.S)
    if m:
        return int(m.group(1))
    data = pdf.read_bytes()
    return len(re.findall(rb"/Type\s*/Page[^s]", data)) or 1


def compile_tex(tex_path: Path, out_pdf: Path, assets_dir: Path | None = None,
                timeout: int = 120) -> CompileResult:
    engine = tex_engine()
    if engine is None:
        return CompileResult(False, None, 0, "No TeX engine found (install MacTeX/BasicTeX or `brew install tectonic`)")
    with tempfile.TemporaryDirectory(prefix="jp-tex-") as tmp:
        work = Path(tmp)
        if assets_dir and assets_dir.is_dir():          # .cls/.sty/images next to the base CV
            for f in assets_dir.iterdir():
                if f.is_file() and f.suffix.lower() in {".cls", ".sty", ".png", ".jpg", ".jpeg", ".pdf", ".bib"} \
                        and f.name != out_pdf.name:
                    shutil.copy2(f, work / f.name)
        src = work / "cv.tex"
        shutil.copy2(tex_path, src)
        cmd = engine + [src.name]
        try:
            proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout)
            if engine[0] == "pdflatex" and proc.returncode == 0:   # second pass for refs
                proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return CompileResult(False, None, 0, "TeX compile timed out")
        log_file = work / "cv.log"
        log = log_file.read_text(errors="replace") if log_file.exists() else proc.stdout + proc.stderr
        pdf = work / "cv.pdf"
        if proc.returncode != 0 or not pdf.exists():
            errs = "\n".join(l for l in log.splitlines() if l.startswith("!"))[:800]
            return CompileResult(False, None, 0, errs or log[-800:])
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf, out_pdf)
        return CompileResult(True, out_pdf, _count_pages(out_pdf, log))
