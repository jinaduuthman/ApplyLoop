"""SQLite job/application log — single source of truth for the agent.

Tables
  jobs       one row per unique job (dedup by canonical URL, then company+title+city fingerprint)
  events     status history (every transition, with detail) — audit trail
  questions  screening questions seen while applying, with the answer used and its source
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from .urls import canonical_url, external_id, fingerprint

# Lifecycle
FOUND, FILTERED, SCORED, LOW_SCORE, QUEUED, APPROVED = (
    "found", "filtered", "scored", "low_score", "queued", "approved")
APPLYING, APPLIED, NEEDS_INPUT, FAILED, MANUAL, SKIPPED, EXPIRED = (
    "applying", "applied", "needs_input", "failed", "manual", "skipped", "expired")
RESPONDED, INTERVIEW, OFFER, REJECTED = "responded", "interview", "offer", "rejected"
STATUSES = [FOUND, FILTERED, SCORED, LOW_SCORE, QUEUED, APPROVED, APPLYING, APPLIED,
            NEEDS_INPUT, FAILED, MANUAL, SKIPPED, EXPIRED, RESPONDED, INTERVIEW, OFFER, REJECTED]
SUBMITTED_STATUSES = {APPLIED, RESPONDED, INTERVIEW, OFFER, REJECTED}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_url TEXT UNIQUE NOT NULL,
    url TEXT, source TEXT, external_id TEXT, fingerprint TEXT,
    title TEXT, company TEXT, location TEXT, salary TEXT, posted TEXT,
    easy_apply INTEGER DEFAULT 0, apply_url TEXT, description TEXT,
    track TEXT, prefilter REAL, score REAL, grade TEXT, recommendation TEXT, eval_json TEXT,
    status TEXT NOT NULL DEFAULT 'found', status_detail TEXT,
    cv_pdf TEXT, cv_tex TEXT, cv_tailored INTEGER DEFAULT 0, tailor_notes TEXT,
    cover_letter TEXT, apply_method TEXT, confirmation TEXT,
    found_at TEXT, updated_at TEXT, applied_at TEXT, notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_fp ON jobs(fingerprint);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id), at TEXT, status TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT, provider TEXT, model TEXT, task TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cost REAL
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id), at TEXT, question TEXT, field_type TEXT,
    options TEXT, answer TEXT, source TEXT, required INTEGER DEFAULT 0
);
"""

JOB_FIELDS = {"url", "source", "title", "company", "location", "salary", "posted", "easy_apply",
              "apply_url", "description", "track", "prefilter", "score", "grade", "recommendation",
              "eval_json", "status_detail", "cv_pdf", "cv_tex", "cv_tailored", "tailor_notes",
              "cover_letter", "apply_method", "confirmation", "notes", "applied_at"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        import threading
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.listeners: list = []   # callables(job_row) — e.g. Google Sheets sync

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    # ── jobs ──────────────────────────────────────────────────────────
    def upsert_job(self, url: str, **fields: Any) -> tuple[int, bool]:
        """Insert a newly found job. Returns (id, is_new). Existing jobs are not overwritten,
        except empty fields get filled (e.g. description fetched later)."""
        canon = canonical_url(url)
        fp = fingerprint(fields.get("company", ""), fields.get("title", ""), fields.get("location", ""))
        row = self.conn.execute("SELECT * FROM jobs WHERE canonical_url=?", (canon,)).fetchone()
        if row is None and fields.get("company") and fields.get("title"):
            row = self.conn.execute("SELECT * FROM jobs WHERE fingerprint=?", (fp,)).fetchone()
        if row is not None:
            fill = {k: v for k, v in fields.items() if k in JOB_FIELDS and v and not row[k]}
            if fill:
                self.update_job(row["id"], **fill)
            return row["id"], False
        data = {k: v for k, v in fields.items() if k in JOB_FIELDS}
        data.update(canonical_url=canon, url=url, external_id=external_id(url), fingerprint=fp,
                    status=FOUND, found_at=_now(), updated_at=_now())
        cols = ", ".join(data)
        with self.tx() as c:
            cur = c.execute(f"INSERT INTO jobs ({cols}) VALUES ({', '.join('?' * len(data))})",
                            list(data.values()))
            job_id = cur.lastrowid
            c.execute("INSERT INTO events (job_id, at, status, detail) VALUES (?,?,?,?)",
                      (job_id, _now(), FOUND, fields.get("source", "")))
        return job_id, True

    def update_job(self, job_id: int, **fields: Any) -> None:
        data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                for k, v in fields.items() if k in JOB_FIELDS}
        if not data:
            return
        data["updated_at"] = _now()
        sets = ", ".join(f"{k}=?" for k in data)
        with self.tx() as c:
            c.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*data.values(), job_id])
        self._notify(job_id)

    def set_status(self, job_id: int, status: str, detail: str = "", **fields: Any) -> None:
        if status not in STATUSES:
            raise ValueError(f"Unknown status {status!r}; use one of {STATUSES}")
        if status == APPLIED and "applied_at" not in fields:
            fields["applied_at"] = _now()
        data = {k: v for k, v in fields.items() if k in JOB_FIELDS}
        data.update(status=status, status_detail=detail, updated_at=_now())
        sets = ", ".join(f"{k}=?" for k in data)
        with self.tx() as c:
            c.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*data.values(), job_id])
            c.execute("INSERT INTO events (job_id, at, status, detail) VALUES (?,?,?,?)",
                      (job_id, _now(), status, detail))
        self._notify(job_id)

    def get(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def by_status(self, *statuses: str, limit: int = 1000) -> list[sqlite3.Row]:
        q = ",".join("?" * len(statuses))
        return self.conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({q}) ORDER BY score DESC, id LIMIT ?",
            [*statuses, limit]).fetchall()

    def all_jobs(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()

    def events(self, job_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()

    def submitted_on(self, day: date | None = None) -> int:
        d = (day or date.today()).isoformat()
        return self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE applied_at LIKE ?", (f"{d}%",)).fetchone()[0]

    def counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status")}

    # ── questions ────────────────────────────────────────────────────
    def record_question(self, job_id: int, question: str, answer: str, source: str,
                        field_type: str = "", options: list[str] | None = None,
                        required: bool = False) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO questions (job_id, at, question, field_type, options, answer,"
                      " source, required) VALUES (?,?,?,?,?,?,?,?)",
                      (job_id, _now(), question, field_type, json.dumps(options or []), answer,
                       source, int(required)))

    def unanswered_questions(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT question, field_type, options, COUNT(*) AS n, MAX(at) AS last FROM questions "
            "WHERE source IN ('none', 'ai') GROUP BY lower(question) ORDER BY n DESC").fetchall()

    def questions_for(self, job_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM questions WHERE job_id=? ORDER BY id",
                                 (job_id,)).fetchall()

    # ── AI usage ─────────────────────────────────────────────────────
    def record_usage(self, u) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO llm_usage (at, provider, model, task, input_tokens, output_tokens, cost)"
                      " VALUES (?,?,?,?,?,?,?)", (_now(), u.provider, u.model, u.task or "default",
                                                  u.input_tokens, u.output_tokens, u.cost))

    def usage_summary(self, since: str = "") -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT model, task, COUNT(*) AS calls, SUM(input_tokens) AS inp, SUM(output_tokens) AS out,"
            " SUM(cost) AS cost, SUM(cost IS NULL) AS unpriced FROM llm_usage WHERE at >= ?"
            " GROUP BY model, task ORDER BY cost DESC", (since,)).fetchall()

    def _notify(self, job_id: int) -> None:
        if not self.listeners:
            return
        row = self.get(job_id)
        for fn in self.listeners:
            try:
                fn(row)
            except Exception:
                pass
