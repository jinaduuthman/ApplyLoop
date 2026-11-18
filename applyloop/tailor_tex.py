"""Choose, tailor and compile a LaTeX CV for one job.

Truthfulness guard — the core rule: a tailored CV may only *surface* skills that
already appear somewhere in your own CVs (any track). Skills the JD wants that you
don't have are reported as gaps and are never written into the CV. Every rewritten
bullet must keep exactly the same numbers/metrics as the original.

Pipeline
  1. keyword gap: JD terms you have (in any CV) but the chosen base CV doesn't show
  2. decide: tailor only if that gap is non-empty or coverage is low (apply.tailor=auto)
  3. deterministic edits: add missing-but-true skills to the best-matching skills row,
     reorder bullets inside each role by JD relevance
  4. optional AI edits (if a provider is configured): rewrite summary + a few bullets,
     each edit validated (numbers, forbidden terms, length, LaTeX safety) or discarded
  5. compile; if over the page limit, undo growth then drop least-relevant bullets;
     if it still doesn't fit, fall back to the untouched base CV
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from .keywords import _extract_tech_keywords
from .latex_cv import TexCV, braces_balanced, compile_tex, latex_to_text, numbers_in, tex_engine
from .llm import LLM, untrusted
from .settings import Settings, Track

console = Console()

GLOBAL_TECH = [
    "python", "sql", "scala", "java", "go", "golang", "rust", "c++", "r", "typescript", "javascript",
    "spark", "pyspark", "flink", "beam", "kafka", "kinesis", "pub/sub", "airflow", "dagster", "prefect",
    "dbt", "fivetran", "airbyte", "snowflake", "snowpark", "redshift", "bigquery", "databricks",
    "delta lake", "iceberg", "hudi", "trino", "presto", "hive", "hadoop", "postgresql", "mysql",
    "mongodb", "cassandra", "dynamodb", "elasticsearch", "opensearch", "redis", "neo4j",
    "aws", "azure", "gcp", "s3", "emr", "glue", "lambda", "athena", "sagemaker", "vertex ai", "bedrock",
    "terraform", "docker", "kubernetes", "helm", "github actions", "jenkins", "ci/cd",
    "great expectations", "data quality", "data governance", "data modeling", "dimensional modeling",
    "kimball", "cdc", "debezium", "reverse etl", "lakehouse", "data mesh", "streaming",
    "pytorch", "tensorflow", "jax", "scikit-learn", "xgboost", "lightgbm", "hugging face",
    "transformers", "llm", "llms", "rag", "langchain", "llamaindex", "vllm", "triton", "onnx",
    "tensorrt", "cuda", "ray", "kubeflow", "mlflow", "feature store", "feast", "pinecone",
    "weaviate", "milvus", "faiss", "pgvector", "vector search", "fine-tuning", "lora", "rlhf",
    "dpo", "distillation", "nlp", "computer vision", "recommender systems", "learning to rank",
    "a/b testing", "causal inference", "experimental design", "statistics", "time series",
    "fastapi", "flask", "rest apis", "graphql", "tableau", "power bi", "looker", "sigma",
    "mlops", "deep learning", "machine learning", "reinforcement learning", "phd", "publications",
]


def term_re(term: str) -> re.Pattern:
    return re.compile(r"(?<![\w+#-])" + re.escape(term.lower()) + r"(?![\w+#-])")


def has_term(text_lower: str, term: str) -> bool:
    return bool(term_re(term).search(text_lower))


def skill_terms(cv: TexCV) -> dict[str, tuple[str, str]]:
    """term(lower) -> (original casing, skills-row label) from a CV's skills table."""
    out: dict[str, tuple[str, str]] = {}
    for row in cv.skills:
        label = latex_to_text(row.label).strip()
        raw = latex_to_text(row.value).rstrip(". ")
        for piece in re.split(r"[,()]", raw):
            piece = piece.strip(" .")
            if not piece or len(piece) > 40:
                continue
            variants = [piece] + ([p.strip() for p in piece.split("/")] if "/" in piece else [])
            for v in variants:
                if v and v.lower() not in out:
                    out[v.lower()] = (v, label)
    return out


@dataclass
class Gap:
    jd_terms: list[str]
    in_base: list[str]
    have_elsewhere: list[str]      # true skills (in some CV) missing from this base CV
    lacking: list[str]             # JD asks, no CV has it → never added
    coverage: float                # share of JD terms the base CV shows


def keyword_gap(jd_text: str, base: TexCV, corpus: list[TexCV], track: Track | None = None,
                all_tracks: list[Track] | None = None) -> Gap:
    jd = (jd_text or "").lower()
    vocab: set[str] = set(GLOBAL_TECH) | set(_extract_tech_keywords(jd_text or ""))
    for cv in corpus:
        vocab |= set(skill_terms(cv))
    for tr in (all_tracks or ([track] if track else [])):
        vocab |= set(tr.keywords)
    vocab = {v for v in vocab if len(v) > 1 or v in ("r",)}
    vocab.discard("r")                      # too ambiguous in prose
    jd_terms = sorted(t for t in vocab if has_term(jd, t))
    base_text = base.plain_text().lower()
    corpus_text = " ".join(cv.plain_text().lower() for cv in corpus)
    in_base = [t for t in jd_terms if has_term(base_text, t)]
    elsewhere = [t for t in jd_terms if t not in in_base and has_term(corpus_text, t)]
    lacking = [t for t in jd_terms if t not in in_base and t not in elsewhere]
    cov = len(in_base) / len(jd_terms) if jd_terms else 1.0
    return Gap(jd_terms, in_base, elsewhere, lacking, cov)


@dataclass
class TailorResult:
    track: str
    pdf: Path | None
    tex: Path | None
    tailored: bool
    pages: int = 0
    notes: list[str] = field(default_factory=list)
    gap: Gap | None = None
    error: str = ""


# ── deterministic edits ─────────────────────────────────────────────

def _label_words(label: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", label.lower()) if len(w) > 2}


def inject_skills(base: TexCV, terms: list[str], corpus: list[TexCV], limit: int = 6) -> list[str]:
    """Add true-but-missing skills to the most similar row of the base skills table."""
    if not base.skills:
        return []
    source: dict[str, tuple[str, str]] = {}
    for cv in corpus:
        for k, v in skill_terms(cv).items():
            source.setdefault(k, v)
    added = []
    for term in terms:
        if len(added) >= limit or term not in source:
            continue
        orig, src_label = source[term]
        if "/" in orig and orig.lower() != term:
            continue
        lw = _label_words(src_label)
        best = max(base.skills, key=lambda r: len(lw & _label_words(latex_to_text(r.label))))
        if not (lw & _label_words(latex_to_text(best.label))):
            # no label match: pick the row whose existing items co-occur with the term's source row
            src_items = {k for k, v in source.items() if v[1] == src_label}
            best = max(base.skills, key=lambda r: sum(1 for k in src_items if has_term(r.value.lower(), k)))
            if not any(has_term(best.value.lower(), k) for k in src_items):
                continue
        value = best.value.rstrip()
        trailing = "." if value.endswith(".") else ""
        value = value.rstrip(".") + f", {orig}{trailing}"
        base.set_skill(best.id, value)
        added.append(f"skills[{latex_to_text(best.label).strip()}] += {orig}")
    return added


def reorder_by_relevance(base: TexCV, jd_terms: list[str]) -> list[str]:
    notes = []
    for role in base.roles:
        def hits(b):
            t = latex_to_text(b.text).lower()
            return sum(1 for term in jd_terms if has_term(t, term))
        order = sorted(role.bullets, key=lambda b: -hits(b))      # stable sort
        ids = [b.id for b in order]
        if ids != [b.id for b in role.bullets]:
            base.reorder(role.id, ids)
            notes.append(f"reordered bullets in '{latex_to_text(role.title).strip()}'")
    return notes


# ── AI edits ─────────────────────────────────────────────────────────

AI_PROMPT = """You are tailoring a LaTeX resume for one job. Improve keyword alignment WITHOUT
inventing anything.

Hard rules:
- Use only facts present in the resume below. Never add employers, titles, dates, tools,
  metrics or responsibilities that are not already there.
- You MAY mention these skills (the candidate has them elsewhere): {allowed}
- You MUST NOT mention these (candidate lacks them): {forbidden}
- Every rewritten bullet must contain exactly the same numbers as its original.
- Keep LaTeX intact (e.g. \\%, \\,, $\\times$, \\href{{...}}{{...}}). Escape % and & as \\% and \\&.
- Keep each bullet about the same length (never more than 15% longer).
- Rewrite at most {max_bullets} bullets — only where it clearly helps for this job.

Resume (structured):
{outline}

Job: {title} at {company}
{jd}

Return JSON:
{{"summary": "<rewritten summary or empty to keep>",
  "bullets": {{"<bullet id>": "<rewritten bullet>"}},
  "notes": ["<one line per change, why>"]}}"""


def validate_bullet(old: str, new: str, forbidden: list[str]) -> str | None:
    if not new or not new.strip():
        return "empty"
    if numbers_in(old) != numbers_in(new):
        return f"numbers changed {sorted(numbers_in(old))} -> {sorted(numbers_in(new))}"
    if len(new) > len(old) * 1.15 + 10:
        return "too long"
    if not braces_balanced(new):
        return "unbalanced braces"
    low_new, low_old = latex_to_text(new).lower(), latex_to_text(old).lower()
    for t in forbidden:
        if has_term(low_new, t) and not has_term(low_old, t):
            return f"adds skill you don't have: {t}"
    return None


def validate_summary(old: str, new: str, forbidden: list[str], corpus_text: str) -> str | None:
    if not new or not new.strip():
        return "empty"
    if not numbers_in(new) <= numbers_in(corpus_text):
        return "introduces new numbers"
    if len(new) > len(old) * 1.2 + 20:
        return "too long"
    if not braces_balanced(new):
        return "unbalanced braces"
    low = latex_to_text(new).lower()
    for t in forbidden:
        if has_term(low, t) and not has_term(latex_to_text(old).lower(), t):
            return f"adds skill you don't have: {t}"
    return None


def ai_edits(base: TexCV, gap: Gap, title: str, company: str, jd_text: str, llm: LLM,
             corpus_text: str, max_bullets: int = 5) -> tuple[list[str], dict[str, str]]:
    """Returns (notes, original_bullets_for_rollback)."""
    prompt = AI_PROMPT.format(
        allowed=", ".join(gap.have_elsewhere + gap.in_base) or "(none)",
        forbidden=", ".join(gap.lacking) or "(none)",
        max_bullets=max_bullets, outline=json.dumps(base.outline(), indent=1),
        title=title, company=company, jd=untrusted(jd_text, 7000))
    data = llm.complete_json(prompt, max_tokens=3000)
    notes, originals = [], {}
    new_sum = (data.get("summary") or "").strip()
    if new_sum and new_sum != base.summary:
        why = validate_summary(base.summary, new_sum, gap.lacking, corpus_text)
        if why:
            notes.append(f"AI summary rejected ({why})")
        else:
            base.set_summary(new_sum)
            notes.append("AI rewrote summary")
    for bid, text in list((data.get("bullets") or {}).items())[:max_bullets]:
        b = base.bullet(bid)
        if not b:
            continue
        why = validate_bullet(b.text, text, gap.lacking)
        if why:
            notes.append(f"AI edit {bid} rejected ({why})")
            continue
        originals[bid] = b.text
        base.set_bullet(bid, text)
        notes.append(f"AI rewrote {bid}")
    notes += [f"  · {n}" for n in data.get("notes", [])[:8] if isinstance(n, str)]
    return notes, originals


# ── orchestration ────────────────────────────────────────────────────

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "job"


def person_name(root: Path) -> str:
    """'Jane Smith' (config/profile.yml) → 'JaneSmith' for CV file names."""
    try:
        import yaml
        data = yaml.safe_load((Path(root) / "config" / "profile.yml").read_text(encoding="utf-8")) or {}
        full = (data.get("candidate") or data).get("full_name", "")
    except Exception:
        full = ""
    return "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", full)) or "Resume"


def base_pdf(track: Track, out_dir: Path, name: str = "") -> TailorResult:
    """The untouched CV for a track (compiled once and cached, or the fallback PDF)."""
    if track.is_tex and track.cv and track.cv.exists() and tex_engine():
        name = name or person_name(out_dir.parent.parent)
        out = out_dir / f"{name}_{track.tag}_Base.pdf"           # e.g. JaneSmith_DE_Base.pdf
        if out.exists() and out.stat().st_mtime >= track.cv.stat().st_mtime:
            return TailorResult(track.id, out, track.cv, False, notes=["base CV (cached)"])
        res = compile_tex(track.cv, out, assets_dir=track.cv.parent)
        if res.ok:
            return TailorResult(track.id, out, track.cv, False, res.pages, ["base CV"])
        console.print(f"[yellow]Base CV compile failed: {res.log_tail[:200]}[/yellow]")
    if track.fallback_pdf and track.fallback_pdf.exists():
        return TailorResult(track.id, track.fallback_pdf, None, False, notes=["fallback PDF (no TeX)"])
    if track.cv and track.cv.suffix.lower() == ".pdf" and track.cv.exists():
        return TailorResult(track.id, track.cv, None, False, notes=["PDF CV"])
    return TailorResult(track.id, None, None, False, error=f"No usable CV for track {track.id}")


def prepare_cv(settings: Settings, track: Track, job_id: int, title: str, company: str,
               jd_text: str, llm: LLM | None = None, mode: str | None = None) -> TailorResult:
    mode = (mode or settings.apply.get("tailor", "auto")).lower()
    out_dir = settings.root / "output" / "cvs"
    if not (track.is_tex and track.cv and track.cv.exists()):
        return base_pdf(track, out_dir)

    corpus = [TexCV.load(t.cv) for t in settings.tracks if t.is_tex and t.cv and t.cv.exists()]
    base = TexCV.load(track.cv)
    gap = keyword_gap(jd_text, base, corpus, track, settings.tracks)
    needs = bool(gap.have_elsewhere) or gap.coverage < 0.6
    if mode == "never" or (mode == "auto" and not needs) or not tex_engine():
        res = base_pdf(track, out_dir)
        res.gap = gap
        if not tex_engine():
            res.notes.append("tailoring skipped: no TeX engine")
        return res

    notes = inject_skills(base, gap.have_elsewhere, corpus)
    notes += reorder_by_relevance(base, gap.jd_terms)
    originals: dict[str, str] = {}
    if llm and llm.available:
        try:
            ai_notes, originals = ai_edits(base, gap, title, company, jd_text, llm,
                                           " ".join(c.plain_text() for c in corpus))
            notes += ai_notes
        except Exception as e:
            notes.append(f"AI tailoring skipped: {e}")

    # one folder per job, the file itself named for recruiters: JaneSmith_DE.pdf
    job_dir = out_dir / f"{job_id:04d}-{_slug(company)}"
    job_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{person_name(settings.root)}_{track.tag}"
    tex_out = job_dir / f"{stem}.tex"
    pdf_out = job_dir / f"{stem}.pdf"

    for attempt in range(8):
        base.save(tex_out)
        res = compile_tex(tex_out, pdf_out, assets_dir=track.cv.parent)
        if not res.ok:
            notes.append(f"compile failed: {res.log_tail[:200]}")
            break
        if res.pages <= track.max_pages:
            return TailorResult(track.id, pdf_out, tex_out, True, res.pages, notes, gap)
        # too long: first roll back AI growth, then trim least relevant bullets of older roles
        if originals:
            for bid, text in originals.items():
                b = base.bullet(bid)
                if b:
                    base.lines[b.line] = f"{b.indent}\\item {text}"
            notes.append(f"over {track.max_pages} page(s): reverted AI bullet rewrites")
            originals = {}
            continue
        roles = [r for r in reversed(base.roles) if len(r.bullets) > 3]
        if not roles:
            break
        victim = roles[0].bullets[-1]
        base.drop_bullet(victim.id)
        notes.append(f"over {track.max_pages} page(s): dropped {victim.id} from '{latex_to_text(roles[0].title).strip()}'")

    res = base_pdf(track, out_dir)
    res.gap = gap
    res.notes = notes + ["fell back to base CV (tailored version didn't fit or compile)"]
    return res


def gap_report(gap: Gap) -> str:
    return (f"coverage {gap.coverage:.0%} | surfaced from your other CVs: {', '.join(gap.have_elsewhere) or '—'}"
            f" | real gaps (not added): {', '.join(gap.lacking) or '—'}")
