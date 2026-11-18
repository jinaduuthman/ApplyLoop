"""The job-search agent: discover → triage → score → pick/tailor CV → apply → log.

    hunt    search Indeed / Glassdoor (+ company ATS boards), dedupe, filter, score,
            prepare a CV per job. In review mode it then applies with you at the wheel;
            in queue mode it stops at `queued` for you to approve.
    submit  apply to approved jobs (queue mode) — or to specific ids.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import store as S
from .urls import canonical_url
from .answers import AnswerBank
from .browser import ManualMode, open_context
from .config import ProjectPaths, load_profile
from .cover import cover_letter_pdf, write_cover_letter
from .evaluator import evaluate_text, generate_report_markdown, recommendation_for
from .filters import filter_reason
from .jd_extractor import ExtractedJD
from .latex_cv import TexCV, latex_to_text
from .llm import LLM, LLMPool
from .router import route
from .settings import Settings, Track, load_settings
from .sources import BROWSER_SOURCES, JobPosting, ats_api, email_alerts, glassdoor, indeed, job_apis
from .tailor_tex import keyword_gap, prepare_cv

console = Console()

BROWSER_SEARCH = {"indeed": indeed, "glassdoor": glassdoor}


@dataclass
class HuntSummary:
    found: int = 0
    new: int = 0
    filtered: int = 0
    low_score: int = 0
    queued: int = 0
    applied: int = 0
    needs_input: int = 0
    failed: int = 0
    manual: int = 0
    skipped: int = 0
    seen: int = 0                                     # already in your list from an earlier run
    skip_reasons: dict = field(default_factory=dict)  # why postings were skipped, by kind
    queued_ids: list[int] = field(default_factory=list)

    def skip(self, kind: str) -> None:
        self.skipped += 1
        self.skip_reasons[kind] = self.skip_reasons.get(kind, 0) + 1


def skip_kind(reason: str) -> str:
    """Group a filter_reason() string for the hunt summary."""
    r = (reason or "").lower()
    for key, kind in (("title doesn't match", "not your roles (title)"), ("title excludes", "excluded title word"),
                      ("posted", "too old"), ("salary", "pay too low"), ("location", "location"),
                      ("remote outside", "location"), ("work mode", "work mode"), ("staffing", "agency / contract"),
                      ("agency", "agency / contract"), ("hourly", "agency / contract"), ("w2", "agency / contract"),
                      ("clearance", "security clearance"), ("blocklisted", "blocked company")):
        if key in r:
            return kind
    return "other filters"


class Agent:
    def __init__(self, paths: ProjectPaths | None = None, settings: Settings | None = None,
                 interactive: bool = True, store: S.Store | None = None, llm: LLM | None = None,
                 provider: str | None = None):
        self.paths = paths or ProjectPaths()
        self.settings = settings or load_settings(self.paths)
        self.interactive = interactive
        self.store = store or S.Store(self.settings.db_path)
        self.pool = LLMPool(self.settings.llm, usage_cb=self.store.record_usage, provider=provider)
        self._fixed_llm = llm            # tests / callers can force one client for every task
        self.llm = llm or self.pool.default
        self.profile = load_profile(self.paths)
        self._cv_text: dict[str, str] = {}
        if self.settings.sheets.get("enabled"):
            from .sheets import attach
            attach(self.store, self.settings.sheets)

    # ── helpers ──────────────────────────────────────────────────────
    def ai(self, task: str) -> LLM:
        """Client for a task: score | route | tailor | answers | cover (config llm.tasks)."""
        return self._fixed_llm or self.pool.for_task(task)

    def cv_text(self, track: Track) -> str:
        if track.id not in self._cv_text:
            text = ""
            if track.is_tex and track.cv and track.cv.exists():
                src = track.cv.read_text(encoding="utf-8")
                text = latex_to_text(src.split("\\begin{document}", 1)[-1])
            elif self.paths.cv.exists():
                text = self.paths.cv.read_text(encoding="utf-8")
            self._cv_text[track.id] = text
        return self._cv_text[track.id]

    def bank(self, track: Track | None) -> AnswerBank:
        return AnswerBank.load(self.paths.config_dir / "answers.yml", self.profile, self.ai("answers"),
                               self.cv_text(track) if track else "", track.id if track else "")

    def already_applied(self, row) -> int | None:
        """Id of a job you already applied to (or that's being applied to) for the same role —
        same title, same employer even under a name variant — or None."""
        from .urls import same_company, title_key
        tk = title_key(row["title"])
        if not tk:
            return None
        for r in self.store.by_status(S.APPLIED, S.APPLYING, S.NEEDS_INPUT):
            if r["id"] != row["id"] and title_key(r["title"]) == tk and same_company(r["company"], row["company"]):
                return r["id"]
        return None

    def job_age_days(self, row) -> float | None:
        """How old the posting is now: the employer/board posting date when known,
        otherwise how long ago applyloop found it."""
        from datetime import datetime
        from .filters import parse_posted_days
        age = parse_posted_days(row["posted"], row["found_at"] or "")
        if age is None and row["found_at"]:
            try:
                age = (datetime.now() - datetime.fromisoformat(row["found_at"][:19])).total_seconds() / 86400
            except ValueError:
                age = None
        return age

    def max_job_age(self, override: int | None = None) -> float:
        """Auto-apply age limit: apply.max_job_age_days (default: search.posted_within_days); 0 = none."""
        if override is not None:
            return float(override)
        v = self.settings.apply.get("max_job_age_days")
        return float(v if v is not None else self.settings.search.get("posted_within_days", 7) or 0)

    def _fresh(self, rows, limit: float, where: str) -> list:
        """Only jobs posted within `limit` days (0 = all). Older ones are just skipped this
        time — they keep their status and stay on your list."""
        if not limit:
            return list(rows)
        fresh = [r for r in rows if (self.job_age_days(r) or 0) <= limit]
        if len(fresh) < len(rows):
            console.print(f"[dim]{len(rows) - len(fresh)} jobs older than {limit:g} days hidden from {where} "
                          f"(still saved; change with --max-age)[/dim]")
        return fresh

    @property
    def run_tracks(self) -> list[Track]:
        """Active tracks plus any paused track asked for with `-t` in this run."""
        extra = [t for t in getattr(self, "_extra_tracks", []) if t not in self.settings.tracks]
        return list(self.settings.tracks) + extra

    def _restore_title_filtered(self, days: int = 14) -> int:
        """Jobs dropped as "title doesn't match any track" that match a track in this run
        (e.g. one you just un-paused or asked for with -t) go back to be checked again."""
        from datetime import datetime, timedelta
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        n = 0
        for r in self.store.by_status(S.FILTERED):
            if (r["status_detail"] or "") not in ("title doesn't match any track", "no matching track"):
                continue
            if (r["found_at"] or "") < since:
                continue
            if filter_reason({**dict(r), "description": ""}, self.settings.filters, self.run_tracks,
                             self.settings.search) is None:
                self.store.set_status(r["id"], S.FOUND, "re-checked: matches a track in this run")
                n += 1
        if n:
            console.print(f"[green]{n} earlier-dropped jobs match a track now — checking them again[/green]")
        return n

    def _tracks(self, ids: list[str] | None) -> list[Track]:
        if ids:                                   # `-t <id>` works even for a paused track
            return [t for t in (self.settings.all_tracks or self.settings.tracks) if t.id in ids]
        return list(self.settings.tracks)

    # ── discovery ────────────────────────────────────────────────────
    def discover(self, ctx, sources: list[str], tracks: list[Track], max_queries: int | None,
                 summary: HuntSummary) -> list[int]:
        search = self.settings.search
        from .filters import wanted_modes
        modes = wanted_modes(search)
        remote_only = modes == {"remote"} or bool(search.get("remote_run"))
        if remote_only:
            locations = ["Remote"]                     # search remote listings only
        else:
            locations = [search.get("location", "")] + (["Remote"] if search.get("remote") and
                                                        (not modes or "remote" in modes) else [])
            if search.get("prefer_remote") and "Remote" in locations:
                locations = ["Remote"] + [x for x in locations if x != "Remote"]   # remote first
        days = int(search.get("posted_within_days", 7))
        per_q = int(search.get("max_per_query", 30))
        new_ids: list[int] = []

        from .filters import filter_reason as _fr

        from .urls import role_key
        # roles already in your list (except ones dropped by a filter — e.g. the same title
        # filtered for its city may come back as a Remote posting)
        known_roles = {role_key(r["company"], r["title"]) for r in self.store.all_jobs()
                       if r["status"] not in (S.FILTERED, S.EXPIRED)}
        from .urls import same_company, title_key
        applied_by_title: dict[str, list[str]] = {}            # roles you applied to, any name variant
        for r in self.store.by_status(S.APPLIED, S.APPLYING, S.NEEDS_INPUT):
            applied_by_title.setdefault(title_key(r["title"]), []).append(r["company"] or "")

        def save(postings: list[JobPosting]):
            for p in postings:
                summary.found += 1
                why = _fr({"title": p.title, "company": p.company, "salary": p.salary,
                           "posted": p.posted}, self.settings.filters, tracks, search)
                if why:
                    summary.skip(skip_kind(why))  # irrelevant title / too old / pay too low
                    continue
                if search.get("easy_apply_only") and p.source in BROWSER_SOURCES and not p.easy_apply:
                    continue
                if any(same_company(c, p.company) for c in applied_by_title.get(title_key(p.title), [])):
                    summary.skip("already applied")   # same role, other site / company name
                    continue
                rk = role_key(p.company, p.title)
                if rk in known_roles and not self.store.conn.execute(
                        "SELECT 1 FROM jobs WHERE canonical_url=?", (canonical_url(p.url),)).fetchone():
                    summary.skip("repost of a job you have")   # other city / site
                    continue
                jid, is_new = self.store.upsert_job(p.url, **p.as_fields())
                if is_new:
                    known_roles.add(rk)
                    summary.new += 1
                    new_ids.append(jid)
                else:
                    summary.seen += 1             # same posting found in an earlier run

        if ctx is not None:
            for src in [s for s in sources if s in BROWSER_SEARCH]:
                mod = BROWSER_SEARCH[src]
                for tr in tracks:
                    for q in tr.queries[:max_queries or None]:
                        for loc in locations:
                            console.print(f"[dim]{src}: “{q}” · {loc or 'anywhere'}[/dim]")
                            try:
                                save(mod.search(ctx, q, loc, days, per_q, self.interactive, remote=remote_only))
                            except Exception as e:
                                console.print(f"[yellow]{src} search failed: {e}[/yellow]")
        for src in [s for s in sources if s in job_apis.API_SOURCES]:
            if src == "remotive" and modes and "remote" not in modes:
                continue
            if src == "adzuna" and not job_apis.adzuna_configured():
                console.print("[yellow]adzuna skipped: add ADZUNA_APP_ID / ADZUNA_APP_KEY to .env "
                              "(free at developer.adzuna.com)[/yellow]")
                continue
            for tr in tracks:
                for q in tr.queries[:max_queries or None]:
                    try:
                        if src == "remotive":
                            console.print(f"[dim]remotive: “{q}” · remote[/dim]")
                            save(job_apis.remotive(q, days, per_q))
                        elif src == "teal":
                            from .sources import teal
                            console.print(f"[dim]teal: “{q}”[/dim]")
                            save(teal.search(q, days, per_q, remote=remote_only))
                        else:
                            for loc in locations:
                                console.print(f"[dim]adzuna: “{q}” · {loc or 'anywhere'}[/dim]")
                                save(job_apis.adzuna(q, loc, days, per_q,
                                                     remote=(loc.lower() == "remote")))
                    except Exception as e:
                        console.print(f"[yellow]{src} search failed: {e}[/yellow]")
        if "alerts" in sources:
            cfg = self.settings.email_alerts
            if not email_alerts.configured():
                console.print("[yellow]alerts skipped: add APPLYLOOP_IMAP_USER / APPLYLOOP_IMAP_PASSWORD "
                              "(app password) to .env[/yellow]")
            else:
                try:
                    console.print("[dim]job-alert emails (Indeed / Glassdoor / LinkedIn)…[/dim]")
                    save(email_alerts.fetch(cfg.get("imap_host", "imap.gmail.com"), cfg.get("folder", "INBOX"),
                                            int(cfg.get("lookback_days", 3)), cfg.get("senders")))
                except Exception as e:
                    console.print(f"[yellow]alert emails failed: {e}[/yellow]")
        all_queries = list(dict.fromkeys(q for tr in tracks for q in tr.queries[:max_queries or None]))

        def title_ok(t: str) -> bool:
            return not _fr({"title": t}, self.settings.filters, tracks, search)

        companies = list(search.get("companies") or [])
        cfg = search.get("ats_search") or {}
        if "ats_search" in sources or cfg.get("enabled"):
            companies += self._ats_search(all_queries, days, companies, cfg, save)
        for company in companies:
            try:
                console.print(f"[dim]{company.get('name')}: careers site ({company.get('ats')})[/dim]")
                save(ats_api.fetch_company(company, days, all_queries, title_ok))
            except Exception as e:
                console.print(f"[yellow]{company.get('name')}: {e}[/yellow]")
        return new_ids

    def _ats_search(self, queries, days, configured, cfg, save) -> list[dict]:
        """Employers found by searching Greenhouse / Lever / Ashby / Workable (and YC) for your
        titles, plus ones found on earlier runs. Returns boards to read like configured companies."""
        from .sources import ats_search
        known = {(str(c.get("ats", "")).lower(), str(c.get("slug", "")).lower()) for c in configured}
        remembered = [b for b in ats_search.load_remembered(self.settings.root, int(cfg.get("remember_days", 30)))
                      if (b["ats"], b["slug"]) not in known]
        known |= {(b["ats"], b["slug"]) for b in remembered}
        found: list[dict] = []
        if ats_search.provider():
            console.print(f"[dim]ATS search ({ats_search.provider()}): {', '.join(cfg.get('sites') or ['greenhouse', 'lever', 'ashby', 'workable'])}[/dim]")
            found, direct = ats_search.discover(queries, days, cfg.get("sites"), int(cfg.get("max_queries", 6)),
                                                known, log=lambda m: console.print(f"[yellow]{m}[/yellow]"))
            if direct:
                save(direct)
            if found:
                ats_search.remember(self.settings.root, found)
            console.print(f"[dim]ATS search: {len(found)} new employer boards · {len(remembered)} remembered[/dim]")
        else:
            console.print("[yellow]ats_search skipped: add BRAVE_API_KEY (or SERPAPI_KEY) to .env[/yellow]")
        limit = int(cfg.get("max_boards", 60))
        return (found + remembered)[:limit]

    # ── triage / scoring / CV ────────────────────────────────────────
    def triage(self, ctx, job_id: int, summary: HuntSummary) -> bool:
        job = self.store.get(job_id)
        # 1) free checks on what we already know (title, company, salary, age) — no network
        reason = filter_reason({**dict(job), "description": ""}, self.settings.filters,
                               self.run_tracks, self.settings.search)
        if reason:
            self.store.set_status(job_id, S.FILTERED, reason)
            summary.filtered += 1
            return False
        # 2) fetch the full posting only for jobs that survived
        if not job["description"] and ctx is not None and job["source"] in BROWSER_SEARCH:
            p = JobPosting(url=job["url"], title=job["title"], company=job["company"],
                           source=job["source"], easy_apply=bool(job["easy_apply"]))
            try:
                p = BROWSER_SEARCH[job["source"]].fetch_details(ctx, p, self.interactive)
            except Exception as e:
                console.print(f"[yellow]detail fetch failed for #{job_id}: {e}[/yellow]")
            if p.extra.get("expired"):
                self.store.set_status(job_id, S.EXPIRED, "posting expired")
                return False
            if not p.description:
                self.store.update_job(job_id, status_detail="no description yet (bot check / page change?)")
                return False
            self.store.update_job(job_id, description=p.description, easy_apply=int(p.easy_apply),
                                  apply_url=p.apply_url)
            job = self.store.get(job_id)
        if ((job["source"] or "").endswith("-alert") or self._behind_bot_wall(job)) and \
                self.settings.email_alerts.get("find_on_company_site", True):
            found = self._find_on_company_site(job)
            if found:
                url, desc, posted = found
                self.store.update_job(job_id, apply_url=url, description=desc or job["description"],
                                      notes=f"also posted on company site: {url}")
                if posted:                       # the employer's date beats an alert's "new"
                    self.store.update_job(job_id, posted=posted)
                job = self.store.get(job_id)
        if job["source"] in job_apis.API_SOURCES and job["apply_url"] and "adzuna." in job["apply_url"]:
            # follow the API's redirect to the real posting: full description + where you'd apply
            full, _, _, _, final = _fetch_public(job["apply_url"], job["title"], job["company"], job["location"])
            upd = {}
            if final and final != job["apply_url"]:
                upd["apply_url"] = final
            if len(full) > len(job["description"] or ""):
                upd["description"] = full
            if upd:
                self.store.update_job(job_id, **upd)
                job = self.store.get(job_id)
        # 3) description-based filters (clearance, citizenship, remote, ...)
        reason = filter_reason(dict(job), self.settings.filters, self.run_tracks,
                               self.settings.search)
        if reason:
            self.store.set_status(job_id, S.FILTERED, reason)
            summary.filtered += 1
            return False
        return True

    def _find_on_company_site(self, job):
        """Same job on the employer's own application site (so it can be applied to without
        the job board). Returns (url, description) or None."""
        from .apply_engine.engine import BOT_WALL_HOSTS
        from .urls import _norm
        from .sources import clean_company
        comp = _norm(clean_company(job["company"] or ""))
        if not comp:
            return None

        def title_sim(a: str, b: str) -> float:
            ta = set(re.findall(r"[a-z]+", a.lower())) - {"senior", "sr", "the", "and", "of", "i", "ii", "iii"}
            tb = set(re.findall(r"[a-z]+", b.lower())) - {"senior", "sr", "the", "and", "of", "i", "ii", "iii"}
            return len(ta & tb) / max(1, len(ta | tb))

        for c in self.settings.search.get("companies") or []:          # your configured boards first
            if _norm(c.get("name", "")) == comp:
                try:
                    for p in ats_api.fetch_company(c, 0, [job["title"]]):
                        if title_sim(p.title, job["title"]) >= 0.6:
                            return p.apply_url or p.url, p.description, p.posted
                except Exception:
                    pass
        try:                                                           # the company's own public board
            for p in ats_api.find_board(clean_company(job["company"])):
                if title_sim(p.title, job["title"]) >= 0.6:
                    return p.apply_url or p.url, p.description, p.posted
        except Exception:
            pass
        if job_apis.adzuna_configured():
            try:
                cands = job_apis.adzuna(f"{job['title']} {job['company']}", "", 30, 20)
            except Exception:
                cands = []
            for p in cands:
                if _norm(p.company) != comp or title_sim(p.title, job["title"]) < 0.6:
                    continue
                full, _, _, _, final = _fetch_public(p.apply_url, p.title, p.company, p.location)
                if final and not BOT_WALL_HOSTS.search(final) and "adzuna." not in final:
                    return final, (full or p.description), p.posted
        return None

    # ── where does each job really apply? ───────────────────────────
    def resolve_links(self, ctx, ids: list[int] | None = None, limit: int | None = None,
                      skip_failed: bool = False) -> dict:
        """Open each Adzuna link once in the browser and record where it actually lands
        (employer site / Greenhouse / Workday / Glassdoor…), so the queue shows which jobs
        applyloop can apply to on its own before anything is attempted."""
        from .apply_engine.engine import automation_verdict
        from .apply_engine.engine import AGGREGATOR_HOSTS
        rows = [self.store.get(i) for i in ids] if ids else \
            self.store.by_status(S.QUEUED, S.APPROVED, S.SCORED, S.NEEDS_INPUT, S.MANUAL)
        # job-board-only jobs (Indeed/Glassdoor/LinkedIn): no page to open, just look for the
        # employer's own posting again (new ATS types may find it now)
        for r in [r for r in rows if r and self._behind_bot_wall(r)
                  and not (skip_failed and (r["notes"] or "").startswith("no employer posting"))]:
            job = self._reroute(r["id"])
            if job["status"] == S.FILTERED:
                continue
            if self._behind_bot_wall(job):
                self.store.update_job(r["id"], notes="no employer posting found")
            elif job["status"] == S.MANUAL:
                from .apply_engine.engine import automation_verdict
                if automation_verdict(job["apply_url"])[0]:
                    self.store.set_status(r["id"], S.QUEUED, "employer form found")
                    console.print(f"  #{r['id']} {r['company'][:24]} → {urlparse(job['apply_url']).netloc} [green]auto[/green]")
        rows = [r for r in rows if r and AGGREGATOR_HOSTS.search(r["apply_url"] or r["url"] or "")
                and not (skip_failed and (r["notes"] or "").startswith("adzuna link didn't"))]
        if limit:
            rows = rows[:limit]
        counts = {"auto": 0, "you": 0, "unresolved": 0}
        if not rows or ctx is None:
            return counts
        from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
        page = ctx.new_page()
        try:
            with Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(),
                          console=console) as prog:
                task = prog.add_task("Resolving Adzuna links", total=len(rows))
                for r in rows:
                    final = _land(page, r["apply_url"] or r["url"])
                    prog.advance(task)
                    if final and _looks_closed(page, final):
                        counts.setdefault("expired", 0)
                        counts["expired"] += 1
                        self.store.set_status(r["id"], S.EXPIRED, f"posting closed ({urlparse(final).netloc})")
                        prog.console.print(f"  #{r['id']} {r['company'][:24]} → [dim]closed / no longer available[/dim]")
                        continue
                    if final and "adzuna." not in final:
                        self.store.update_job(r["id"], apply_url=final,
                                              notes=f"adzuna → {urlparse(final).netloc}")
                    job = self._reroute(r["id"])        # listing site / Glassdoor → employer's own board?
                    if job["status"] == S.FILTERED:
                        counts.setdefault("expired", 0)
                        counts["expired"] += 1
                        continue
                    if "adzuna." in (job["apply_url"] or ""):
                        counts["unresolved"] += 1
                        _shot_resolve(page, self.settings.root, r["id"])
                        self.store.update_job(r["id"], notes="adzuna link didn't lead to an employer page")
                        prog.console.print(f"  #{r['id']} {r['company'][:24]} → [dim]stayed on Adzuna[/dim]")
                        continue
                    ok, why = automation_verdict(job["apply_url"] or job["url"])
                    if ok and job["status"] == S.MANUAL:
                        self.store.set_status(r["id"], S.QUEUED, "employer form found")   # back in the auto queue
                    counts["auto" if ok else "you"] += 1
                    prog.console.print(f"  #{r['id']} {r['company'][:24]} → {urlparse(job['apply_url']).netloc} "
                                       f"[{'green' if ok else 'yellow'}]{'auto' if ok else 'you: ' + why}[/]")
        finally:
            try:
                page.close()
            except Exception:
                pass
        console.print(f"[bold]Adzuna links:[/bold] {counts['auto']} applyloop can apply · "
                      f"{counts['you']} need you · {counts['unresolved']} unresolved")
        return counts

    @staticmethod
    def _behind_bot_wall(job) -> bool:
        """The only known way to apply is through a job board with bot checks (Glassdoor/Indeed/…)."""
        from .apply_engine.engine import BOT_WALL_HOSTS, direct_form_url
        target = job["apply_url"] or job["url"] or ""
        return bool(BOT_WALL_HOSTS.search(target)) and not direct_form_url(job)

    def _reroute(self, job_id: int):
        """For a Glassdoor/Indeed job (or one that only leads to a listing site), look for the same
        posting on the employer's own site and switch the application there. Returns the row."""
        from .apply_engine.engine import AGGREGATOR_HOSTS, direct_form_url
        job = self.store.get(job_id)
        target = job["apply_url"] or job["url"] or ""
        if not (self._behind_bot_wall(job) or (AGGREGATOR_HOSTS.search(target) and not direct_form_url(job))):
            return job
        found = self._find_on_company_site(job)
        if found:
            url, desc, posted = found
            self.store.update_job(job_id, apply_url=url,
                                  description=desc if len(desc or "") > len(job["description"] or "") else job["description"],
                                  notes=f"applying on company site instead of {urlparse(job['url'] or '').netloc}: {url}")
            if posted:
                self.store.update_job(job_id, posted=posted)
            job = self.store.get(job_id)
            if self._too_old(job):
                return job
            console.print(f"[green]#{job_id}: found on the company site → {url}[/green]")
            return job
        return job

    def _too_old(self, job) -> bool:
        """Reposts: an alert says 'new' but the employer's posting is weeks old → drop it."""
        from .filters import parse_posted_days
        limit = float(self.settings.search.get("posted_within_days") or 0)
        age = parse_posted_days(job["posted"], job["found_at"] or "")
        if limit and age is not None and age > limit:
            self.store.set_status(job["id"], S.FILTERED, f"posted {age:.0f} days ago on the employer's site (> {limit:.0f})")
            return True
        return False

    def _compute_score(self, job) -> dict:
        """Pure scoring work for one job (no DB writes) — safe to run in parallel threads."""
        out: dict = {"id": job["id"]}
        r = route(job["title"], job["description"] or "", self.run_tracks, self.ai("route"))
        if r.track is None:
            out["filtered"] = "no matching track"
            return out
        track = r.track
        out["track"] = track.id
        coverage = 0.0
        if track.is_tex and track.cv and track.cv.exists():
            gap = keyword_gap(job["description"] or "", self._tex(track.cv), self._corpus(),
                              track, self.run_tracks)
            coverage = gap.coverage
        out["prefilter"] = round(coverage, 2)
        scorer = self.ai("score")
        if scorer.available and job["description"]:
            jd = ExtractedJD(url=job["url"], title=job["title"], company=job["company"],
                             location=job["location"] or "", full_text=job["description"],
                             ats_platform=job["source"] or "")
            profile_yaml = self.paths.profile.read_text(encoding="utf-8") if self.paths.profile.exists() else ""
            res = evaluate_text(jd, self.cv_text(track), profile_yaml, scorer)
            if not res.error:
                out.update(score=res.overall_score, grade=res.grade, result=res)
                return out
            out["warning"] = res.error
        title_hit = 1.0 if any(t in (job["title"] or "").lower() for t in track.titles) else 0.0
        out.update(score=round(5 * (0.6 * coverage + 0.4 * title_hit), 1), grade="kw")
        return out

    def _save_score(self, out: dict) -> float:
        job_id = out["id"]
        if out.get("filtered"):
            self.store.set_status(job_id, S.FILTERED, out["filtered"])
            return 0.0
        if out.get("warning"):
            console.print(f"[yellow]#{job_id}: {out['warning'][:120]} — using keyword score[/yellow]")
        fields = {"track": out["track"], "prefilter": out["prefilter"], "score": out["score"],
                  "grade": out["grade"], "recommendation": recommendation_for(out["score"])}
        res = out.get("result")
        if res is not None:
            report = self.paths.reports_dir / f"{job_id:04d}-{out['track']}.md"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(generate_report_markdown(res, job_id), encoding="utf-8")
            fields.update(recommendation=res.recommendation,
                          eval_json={"summary": res.summary, "strengths": res.strengths,
                                     "gaps": res.gaps, "pitch": res.tailored_pitch, "report": str(report)})
        self.store.update_job(job_id, **fields)
        return out["score"]

    def evaluate(self, job_id: int) -> float:
        return self._save_score(self._compute_score(self.store.get(job_id)))

    def _tex(self, path):
        cache = self.__dict__.setdefault("_tex_cache", {})
        if path not in cache:
            cache[path] = TexCV.load(path)
        return cache[path]

    def _corpus(self):
        return [self._tex(t.cv) for t in self.run_tracks if t.is_tex and t.cv and t.cv.exists()]

    @staticmethod
    def _cv_outdated(job, track) -> bool:
        """The CV prepared for this job is missing, or older than the track's base CV files."""
        from pathlib import Path as _P
        cv = _P(job["cv_pdf"] or "")
        if not cv.exists():
            return True
        if track is None:
            return False
        bases = [p for p in (track.cv, track.fallback_pdf) if p and _P(p).exists()]
        return any(_P(b).stat().st_mtime > cv.stat().st_mtime for b in bases)

    def prepare(self, job_id: int) -> bool:
        job = self.store.get(job_id)
        track = self.settings.track(job["track"])
        res = prepare_cv(self.settings, track, job_id, job["title"], job["company"],
                         job["description"] or "", self.ai("tailor"))
        if not res.pdf:
            self.store.set_status(job_id, S.FAILED, res.error or "no CV")
            return False
        from .tailor_tex import gap_report
        notes = "\n".join(res.notes + ([gap_report(res.gap)] if res.gap else []))
        self.store.update_job(job_id, cv_pdf=str(res.pdf), cv_tex=str(res.tex or ""),
                              cv_tailored=int(res.tailored), tailor_notes=notes)
        return True

    # ── manual intake ────────────────────────────────────────────────
    def add_job(self, url: str, description: str = "", title: str = "", company: str = "",
                location: str = "", salary: str = "", ctx=None) -> int:
        """Add one job you found yourself (any site), then filter → score → prepare CV.
        If no description is given, it is fetched: ATS pages over plain HTTP, Indeed/Glassdoor
        through the browser if one is available."""
        from .urls import canonical_url
        src = "indeed" if "indeed." in url else "glassdoor" if "glassdoor." in url else "manual"
        if not description:
            description, title, company, location, _ = _fetch_public(url, title, company, location)
        jid, is_new = self.store.upsert_job(url, title=title, company=company, location=location,
                                            salary=salary, source=src, description=description,
                                            apply_url="" if src in BROWSER_SEARCH else url)
        if not is_new and self.store.get(jid)["status"] not in (S.FOUND, S.FILTERED):
            console.print(f"[yellow]Already tracked as #{jid} ({self.store.get(jid)['status']}) — "
                          f"{canonical_url(url)}[/yellow]")
            return jid
        if description and not self.store.get(jid)["description"]:
            self.store.update_job(jid, description=description)
        # you picked this job, so the "title must match a track" rule is skipped
        reason = filter_reason(dict(self.store.get(jid)), self.settings.filters, None, self.settings.search)
        if reason:
            self.store.set_status(jid, S.FILTERED, reason)
            console.print(f"[yellow]#{jid} filtered: {reason} (edit filters in config/applyloop.yml)[/yellow]")
            return jid
        score = self.evaluate(jid)
        row = self.store.get(jid)
        if row["status"] == S.FILTERED:
            return jid
        self.store.set_status(jid, S.SCORED, f"{score}/5 via {row['grade']}")
        if self.prepare(jid):
            self.store.set_status(jid, S.QUEUED, "added by you")
        row = self.store.get(jid)
        console.print(f"[green]#{jid} {row['company']} — {row['title']}: {row['score']}/5 "
                      f"({row['track']}) → {row['status']}[/green]\nCV: {row['cv_pdf']}")
        if row["tailor_notes"]:
            console.print(f"[dim]{row['tailor_notes']}[/dim]")
        return jid

    # ── applying ─────────────────────────────────────────────────────
    def _cover_callbacks(self, job, track):
        policy = str(self.settings.apply.get("cover_letter", "required")).lower()
        cache: dict = {}

        def text() -> str:
            if policy == "never" or not self.ai("cover").available:
                return ""
            if "text" not in cache:
                cache["text"] = job["cover_letter"] or write_cover_letter(
                    self.ai("cover"), self.profile, self.cv_text(track), job["title"], job["company"],
                    job["description"] or "")
                self.store.update_job(job["id"], cover_letter=cache["text"])
            return cache["text"]

        def pdf():
            t = text()
            if not t:
                return None
            out = self.settings.root / "output" / "cover-letters" / f"{job['id']:04d}"
            return cover_letter_pdf(t, self.profile, job["company"], out)
        return pdf, text

    def _recruiter_file_name(self, job_id: int, track) -> None:
        """CVs made before the naming change ('base-data_engineering.pdf', '0094-stripe-….pdf') get a
        copy named the way recruiters see it: JaneSmith_DE_Base.pdf / <job folder>/JaneSmith_DE.pdf."""
        import shutil
        from .tailor_tex import _slug, person_name
        job = self.store.get(job_id)
        cur = Path(job["cv_pdf"] or "")
        if not track or not cur.exists() or not re.match(r"^(base-|\d{4}-)", cur.name):
            return
        out = self.settings.root / "output" / "cvs"
        name = person_name(self.settings.root)
        if cur.name.startswith("base-"):
            new = out / f"{name}_{track.tag}_Base.pdf"
        else:
            new = out / f"{job_id:04d}-{_slug(job['company'] or '')}" / f"{name}_{track.tag}.pdf"
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            if not new.exists():
                shutil.copy2(cur, new)
            self.store.update_job(job_id, cv_pdf=str(new))
        except OSError:
            pass

    def apply_one(self, ctx, job_id: int, mode: str, manual: bool = False) -> str:
        from .apply_engine.engine import apply_job, manual_apply

        job = self.store.get(job_id)
        dup = self.already_applied(job)
        if dup:
            self.store.set_status(job_id, S.SKIPPED, f"already applied as #{dup}")
            console.print(f"[dim]#{job_id} {job['company']} — {job['title']}: already applied (#{dup}) — skipped[/dim]")
            return S.SKIPPED
        cap = int(self.settings.apply.get("daily_cap", 20))
        if not manual and self.store.submitted_on() >= cap:     # the cap limits automation, not you
            console.print(f"[yellow]Daily cap reached ({cap}). Stopping.[/yellow]")
            return "cap"
        track = self.settings.track(job["track"]) if job["track"] else None
        if job["cv_pdf"] and self._cv_outdated(job, track):
            console.print(f"[dim]#{job_id}: your base CV changed since this one was made — rebuilding it[/dim]")
            self.store.update_job(job_id, cv_pdf="", cv_tex="")
            job = self.store.get(job_id)
        if not job["cv_pdf"] and not self.prepare(job_id):
            return S.FAILED
        self._recruiter_file_name(job_id, track)
        job = self.store.get(job_id)
        was_approved = job["status"] == S.APPROVED
        console.rule(f"#{job_id} {job['company']} — {job['title']}  ({job['score']}/5, {job['track']})")
        if job["tailor_notes"]:
            console.print(f"[dim]{job['tailor_notes']}[/dim]")
        if not manual and ctx is not None:
            from .apply_engine.engine import automation_verdict, direct_form_url
            if "adzuna." in (job["apply_url"] or job["url"] or ""):
                self.resolve_links(ctx, [job_id])                 # find the real application page first
            job = self._reroute(job_id)
            if self.store.get(job_id)["status"] == S.FILTERED:
                console.print(f"[dim]#{job_id}: {self.store.get(job_id)['status_detail']} — skipped[/dim]")
                return S.FILTERED
            target = job["apply_url"] or job["url"]
            ok, why = automation_verdict(target)
            if not ok and not direct_form_url(job):
                # Glassdoor/Indeed show a human check to automated browsers (applyloop doesn't try to
                # get past it); Workday & co. need your account there. You apply in your own
                # browser with the CV + answers shown, or it's parked for you.
                if self._behind_bot_wall(job):
                    why = "only on a job board with bot checks (Easy Apply / not found on the company site)"
                if not self.interactive:
                    self.store.set_status(job_id, S.MANUAL, f"{why}: {target}")
                    console.print(f"[yellow]#{job_id}: {why} → apply yourself: {target}[/yellow]")
                    return S.MANUAL
                console.print(f"[yellow]{why} — opening it in your own browser.[/yellow]")
                manual = True
        if manual or ctx is None:
            bank = self.bank(track)
            answers = [(q, bank.resolve(q, allow_ai=False).value or "—") for q in
                       ("Are you authorized to work in the US?", "Will you require sponsorship?",
                        "Desired salary", "Years of experience")]
            out = manual_apply(job, Path(job["cv_pdf"]), answers)
        else:
            self.store.set_status(job_id, S.APPLYING, mode)
            pdf_cb, text_cb = self._cover_callbacks(job, track)
            out = apply_job(ctx, self.store, self.store.get(job_id), self.settings, self.bank(track),
                            mode=mode, interactive=self.interactive, resume=Path(job["cv_pdf"]),
                            cover_letter_pdf=pdf_cb, cover_letter_text=text_cb,
                            approved=was_approved, trust_ai=getattr(self, "_trust_ai", False),
                            cover_policy=str(self.settings.apply.get("cover_letter", "required")).lower())
        if out.status == "stop":                               # [q]uit in a hand-off
            self.store.set_status(job_id, S.MANUAL, "left for later")
            return "stop"
        detail = out.detail + (f" | screenshots: {out.screenshots[-1]}" if out.screenshots and out.screenshots[-1] else "")
        self.store.set_status(job_id, out.status, detail, apply_method=out.method)
        color = {"applied": "green", "needs_input": "yellow", "skipped": "dim"}.get(out.status, "red")
        console.print(f"[{color}]#{job_id}: {out.status} — {out.detail}[/{color}]")
        return out.status

    # ── entry points ─────────────────────────────────────────────────
    def hunt(self, mode: str | None = None, sources: list[str] | None = None,
             track_ids: list[str] | None = None, max_queries: int | None = None,
             max_apply: int | None = None, browser_mode: str | None = None) -> HuntSummary:
        mode = (mode or self.settings.apply_mode).lower()
        sources = sources or self.settings.search.get("sources", ["indeed", "glassdoor"])
        auto_cfg = self.settings.apply.get("auto") or {}
        if not track_ids and auto_cfg.get("rotation_only") and self.track_of_the_day():
            track_ids = [self.track_of_the_day()]        # today's track only: search + apply
        tracks = self._tracks(track_ids)
        self._extra_tracks = [t for t in tracks if t.paused]   # `-t <paused track>` runs it this time
        summary = HuntSummary()
        self._restore_title_filtered()
        console.print(f"[bold]applyloop hunt[/bold] · mode={mode} · AI={self._fixed_llm.describe() if self._fixed_llm else self.pool.describe()} · "
                      f"tracks={', '.join(t.id for t in tracks)} · sources={', '.join(sources)}")
        auto = self.settings.apply.get("auto") or {}
        need_browser = any(s in BROWSER_SEARCH for s in sources) or mode == "auto" or \
            (mode == "review" and self.interactive)
        try:
            if not need_browser:
                raise ManualMode()           # API-only discovery; no browser window needed
            cm = open_context(self.settings.browser, browser_mode,
                              headless=bool(auto.get("headless", False)) if mode == "auto" else None)
            ctx = cm.__enter__()
        except ManualMode:
            if need_browser:
                console.print("[yellow]browser.mode=manual — Indeed/Glassdoor can't be searched; "
                              "API sources and company boards still work, applications are handed to you.[/yellow]")
            cm, ctx = None, None
        try:
            new_ids = self.discover(ctx, sources, tracks, max_queries, summary)
            pending = new_ids + [r["id"] for r in self.store.by_status(S.FOUND) if r["id"] not in new_ids]
            console.print(f"[bold]{summary.found} postings · {summary.skipped} skipped · {summary.seen} seen before · "
                          f"{summary.new} new · {len(pending)} to check[/bold]")
            if summary.skip_reasons:
                console.print("[dim]  skipped: " + " · ".join(f"{k} {v}" for k, v in
                              sorted(summary.skip_reasons.items(), key=lambda kv: -kv[1])) + "[/dim]")
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
            with Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(),
                          TimeElapsedColumn(), console=console) as prog:
                t1 = prog.add_task("Checking postings", total=len(pending))
                survivors = []
                for jid in pending:
                    if self.triage(ctx, jid, summary):
                        survivors.append(jid)
                    prog.advance(t1)
                t2 = prog.add_task("Scoring with AI", total=len(survivors))
                workers = int(self.settings.llm.get("parallel", 4) or 1)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = [pool.submit(self._compute_score, self.store.get(j)) for j in survivors]
                    scored = []
                    for f in as_completed(futs):
                        try:
                            out = f.result()
                        except Exception as e:
                            console.print(f"[yellow]scoring error: {e}[/yellow]")
                            prog.advance(t2)
                            continue
                        scored.append((self._save_score(out), out))
                        prog.advance(t2)
                t3 = prog.add_task("Preparing CVs", total=len(scored))
                for score, out in sorted(scored, key=lambda x: -x[0]):
                    jid = out["id"]
                    job = self.store.get(jid)
                    prog.advance(t3)
                    if job["status"] == S.FILTERED:
                        summary.filtered += 1
                        continue
                    track = self.settings.track(job["track"])
                    if score < track.min_score:
                        self.store.set_status(jid, S.LOW_SCORE, f"{score} < {track.min_score}")
                        summary.low_score += 1
                        continue
                    self.store.set_status(jid, S.SCORED, f"{score}/5 via {job['grade']}")
                    if self.prepare(jid):
                        self.store.set_status(jid, S.QUEUED, "ready")
                        summary.queued += 1
                        summary.queued_ids.append(jid)
                        prog.console.print(f"  [green]✓[/green] #{jid} {score}/5 {job['company']} — {job['title']}")

            if ctx is not None and self.settings.search.get("resolve_links", True):
                self.resolve_links(ctx, skip_failed=True)      # where do the Adzuna jobs really apply?
            if mode == "auto":
                self._autopilot(ctx, auto, max_apply, summary,
                                only_tracks=[t.id for t in tracks] if track_ids else None)
            elif mode == "review" and self.interactive:
                # same picking as auto (track, score, duplicates, employer forms first) — but you
                # see every answer and press [s]ubmit yourself
                self._autopilot(ctx, auto, max_apply, summary,
                                only_tracks=[t.id for t in tracks] if track_ids else None, mode="review")
        finally:
            if cm is not None:
                cm.__exit__(None, None, None)
        self.print_summary(summary)
        if mode in ("auto", "review"):
            self.write_digest(summary)
        return summary

    def track_of_the_day(self, day=None) -> str | None:
        """apply.auto.track_rotation: [data_engineering, ml_engineering, research_scientist] →
        one track gets first pick each day, in turn (Mon DE, Tue ML, Wed research, Thu DE, …).
        A {weekday: track} mapping works too: {mon: data_engineering, tue: ml_engineering, ...}."""
        from datetime import date
        rot = (self.settings.apply.get("auto") or {}).get("track_rotation")
        if not rot:
            return None
        day = day or date.today()
        if isinstance(rot, dict):
            names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            t = rot.get(names[day.weekday()]) or rot.get("default")
            return t if t and self.settings.is_active(t) else None
        rot = [r for r in rot if r and self.settings.is_active(r)]
        return rot[day.toordinal() % len(rot)] if rot else None

    def remote_run(self) -> bool:
        """`--remote` (this run only) or search.remote_only in config."""
        return bool(self.settings.search.get("remote_run") or self.settings.search.get("remote_only"))

    @staticmethod
    def _is_remote(row) -> bool:
        from .filters import detect_work_mode
        return detect_work_mode(row["title"] or "", row["location"] or "", row["description"] or "") == "remote"

    def _remote_ok(self, row) -> bool:
        return not self.remote_run() or self._is_remote(row)

    def _order(self, rows, today: str | None = None):
        """Today's track first (if any), then remote jobs first (search.prefer_remote), then best score."""
        pref = bool(self.settings.search.get("prefer_remote"))
        return sorted(rows, key=lambda r: ((today is not None and r["track"] != today),
                                           (pref and not self._is_remote(r)), -(r["score"] or 0)))

    def _autopilot(self, ctx, auto: dict, max_apply: int | None, summary: HuntSummary,
                   only_tracks: list[str] | None = None, mode: str = "queue") -> None:
        """mode=auto: approve + submit queued jobs above auto.min_score where the form can be
        filled without you; everything else is listed in the digest for you."""
        from .apply_engine.engine import automation_verdict, human_delay
        min_score = float(auto.get("min_score", 4.0))
        limit = int(max_apply or auto.get("max_per_run", 10))
        self._trust_ai = bool(auto.get("allow_ai_answers", False))
        done = 0
        from .urls import _norm

        from .urls import role_key

        def key(r):
            return role_key(r["company"], r["title"])
        done_rows = self.store.by_status(S.APPLIED, S.NEEDS_INPUT, S.APPLYING, S.APPROVED)
        seen = {key(r): r["id"] for r in done_rows}
        from .apply_engine.engine import direct_form_url

        def form_of(r):
            return direct_form_url(r) or (r["apply_url"] or "")
        seen_forms = {form_of(r): r["id"] for r in done_rows if form_of(r)}
        # optional: at most N applications per company per 7 days (avoids 6 in a day to one employer)
        per_co = int(auto.get("max_per_company_per_week") or 0)
        from datetime import datetime, timedelta
        week_ago = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        co_count: dict[str, int] = {}
        for r in self.store.by_status(S.APPLIED):
            if (r["applied_at"] or "") >= week_ago:
                co_count[_norm(r["company"] or "")] = co_count.get(_norm(r["company"] or ""), 0) + 1
        rows = self.store.by_status(S.QUEUED)
        if only_tracks:                                     # `hunt -t data_engineering`
            rows = [r for r in rows if r["track"] in only_tracks]
        else:                                               # paused tracks wait in the queue
            rows = [r for r in rows if self.settings.is_active(r["track"])]
        rows = self._fresh(rows, self.max_job_age(), "auto-apply")
        today = self.track_of_the_day()
        if today and not only_tracks:
            if auto.get("rotation_only"):
                rows = [r for r in rows if r["track"] == today]
            console.print(f"[bold]Today's track:[/bold] {today}"
                          + (" (only)" if auto.get("rotation_only") else " first, then the others"))
            rows = self._order(rows, today)
        else:
            rows = self._order(rows)
        for row in rows:
            if done >= limit:
                break
            if (row["score"] or 0) < min_score:
                continue                                  # stays queued — optional review
            if not self._remote_ok(row):
                continue                                  # `--remote` run: local/onsite jobs stay queued
            if per_co and co_count.get(_norm(row["company"] or ""), 0) >= per_co:
                continue                                  # stays queued for next week
            dup = seen.get(key(row)) or seen_forms.get(form_of(row))
            if dup and dup != row["id"]:
                # same company + title, or the very same application form, already applied/attempted
                # (job sites repost one opening many times under different cities)
                self.store.set_status(row["id"], S.SKIPPED, f"duplicate of #{dup}")
                continue
            seen[key(row)] = row["id"]
            if form_of(row):
                seen_forms[form_of(row)] = row["id"]
            row = self._reroute(row["id"])
            if row["status"] == S.FILTERED:
                summary.filtered += 1
                continue
            target = row["apply_url"] or row["url"]
            ok, why = automation_verdict(target)
            if not ok:
                self.store.set_status(row["id"], S.MANUAL, f"{why}: {target}")
                summary.manual += 1
                continue
            if mode == "queue":
                self.store.set_status(row["id"], S.APPROVED, f"auto-approved (score ≥ {min_score})")
            status = self.apply_one(ctx, row["id"], mode)
            if status == S.APPLIED:
                co_count[_norm(row["company"] or "")] = co_count.get(_norm(row["company"] or ""), 0) + 1
            if status == "cap":
                break
            self._tally(summary, status)
            done += 1
            if status == S.APPLIED:
                human_delay(self.settings)

    def write_digest(self, summary: HuntSummary) -> Path:
        """Daily summary of what was done and what needs you (+ macOS notification)."""
        import subprocess
        import sys
        from datetime import date
        out = self.settings.root / "output" / "digests" / f"{date.today().isoformat()}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()

        def rows(status):
            return [r for r in self.store.by_status(status) if (r["updated_at"] or "").startswith(today)]

        lines = [f"# applyloop — {today}", "",
                 f"Found {summary.found} · new {summary.new} · filtered {summary.filtered} · "
                 f"low score {summary.low_score} · queued {summary.queued} · **applied {summary.applied}** · "
                 f"needs you {summary.needs_input + summary.manual}", ""]
        for title, status in (("Applied", S.APPLIED), ("Needs your input (answer, then `applyloop submit <id>`)",
                                                          S.NEEDS_INPUT),
                              ("Apply yourself (job board / account portal)", S.MANUAL),
                              ("Failed", S.FAILED)):
            rs = rows(status)
            if not rs:
                continue
            lines += [f"## {title}", ""]
            for r in rs:
                lines.append(f"- #{r['id']} **{r['company']}** — {r['title']} ({r['score']}/5, {r['track']}) "
                             f"· {r['status_detail'] or ''} · CV: `{r['cv_pdf'] or '—'}`")
            lines.append("")
        out.write_text("\n".join(lines), encoding="utf-8")
        console.print(f"[bold]Digest:[/bold] {out}")
        if sys.platform == "darwin":
            msg = f"Applied {summary.applied} · needs you {summary.needs_input + summary.manual}"
            try:
                subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "applyloop"'],
                               timeout=10, check=False)
            except Exception:
                pass
        return out

    def submit(self, ids: list[int] | None = None, mode: str = "queue", manual: bool = False,
               browser_mode: str | None = None, track_ids: list[str] | None = None,
               limit: int | None = None, max_age: int | None = None) -> HuntSummary:
        summary = HuntSummary()
        if ids:
            rows = [self.store.get(i) for i in ids]
        elif manual:
            # walk the "apply yourself" list: best first, one after another
            rows = self.store.by_status(S.MANUAL)
            rows = [r for r in rows if (r["track"] in track_ids if track_ids else self.settings.is_active(r["track"]))]
            rows = self._order(self._fresh(rows, float(max_age or 0), "your apply-yourself list"))
            console.print(f"[bold]{len(rows)} jobs to apply to yourself[/bold] — each opens in your browser; "
                          "answer y / n / l (later) / q (stop)")
        else:
            rows = self.store.by_status(S.APPROVED)
        rows = [r for r in rows if r is not None and (not track_ids or r["track"] in track_ids)]
        if not ids and not track_ids:                       # paused tracks wait
            rows = [r for r in rows if self.settings.is_active(r["track"])]
        if self.remote_run():
            before = len(rows)
            rows = [r for r in rows if self._remote_ok(r)]
            console.print(f"[bold]Remote only:[/bold] {len(rows)} of {before} jobs are remote")
        if limit:
            rows = rows[:limit]
        if not rows:
            console.print("[dim]Nothing to do. Approve jobs with `applyloop queue approve <ids>`, "
                          "or `applyloop submit --manual` for your apply-yourself list.[/dim]")
            return summary
        try:
            cm = None if manual else open_context(self.settings.browser, browser_mode)
            ctx = cm.__enter__() if cm else None
        except ManualMode:
            cm, ctx, manual = None, None, True
        try:
            for i, row in enumerate(rows):
                status = self.apply_one(ctx, row["id"], mode, manual=manual)
                if status in ("cap", "stop"):
                    break
                self._tally(summary, status)
                if i < len(rows) - 1 and status == S.APPLIED and not manual:
                    from .apply_engine.engine import human_delay
                    human_delay(self.settings)
        finally:
            if cm is not None:
                cm.__exit__(None, None, None)
        self.print_summary(summary)
        return summary

    @staticmethod
    def _tally(summary: HuntSummary, status: str) -> None:
        if status == S.APPLIED:
            summary.applied += 1
        elif status == S.NEEDS_INPUT:
            summary.needs_input += 1
        elif status == S.FAILED:
            summary.failed += 1

    def print_summary(self, s: HuntSummary) -> None:
        t = Table(title="Run summary")
        t.add_column("Metric")
        t.add_column("Count", justify="right")
        for k in ("found", "skipped", "new", "filtered", "low_score", "queued", "applied", "needs_input", "manual", "failed"):
            t.add_row(k.replace("_", " "), str(getattr(s, k)))
        console.print(t)


def _land(page, url: str, wait_s: float = 20) -> str:
    """Follow an aggregator link in the browser to the page it finally lands on."""
    import re as _re
    import time as _time
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    deadline = _time.time() + wait_s
    clicked = False
    while _time.time() < deadline:
        try:
            cur = page.url
        except Exception:
            return ""
        if "adzuna." not in cur:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            return _through_listing(page)
        if not clicked and "/details/" in cur:          # Adzuna's own job page → its Apply link
            try:
                href = page.evaluate("""() => { const a = [...document.querySelectorAll('a[href]')].find(a =>
                    /\\/land\\/ad\\//.test(a.href) || /^\\s*apply/i.test(a.innerText || '')); return a ? a.href : ''; }""")
            except Exception:
                href = ""
            clicked = True
            if href:
                try:
                    page.goto(href, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                continue
        _time.sleep(1)
    try:
        return page.url
    except Exception:
        return ""


CLOSED_RE = re.compile(r"no longer available|no longer accepting|job (has )?expired|position has been filled|"
                       r"job is closed|this job is closed|posting (has )?(closed|expired)|closedJob=True", re.I)


def _looks_closed(page, url: str) -> bool:
    if CLOSED_RE.search(url or ""):
        return True
    try:
        return bool(CLOSED_RE.search(page.inner_text("body")[:3000]))
    except Exception:
        return False


def _shot_resolve(page, root, job_id: int) -> None:
    """Keep a picture of where an unresolved link ended up (output/applications/<id>/resolve.png)."""
    try:
        d = Path(root) / "output" / "applications" / f"{job_id:04d}"
        d.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(d / "resolve.png"))
    except Exception:
        pass


def _through_listing(page) -> str:
    """Landed on another listing site (diversity.com, …)? Follow its Apply link once."""
    from .apply_engine.engine import AGGREGATOR_HOSTS
    if not AGGREGATOR_HOSTS.search(page.url):
        return page.url
    try:
        href = page.evaluate("""() => { const a = [...document.querySelectorAll('a[href]')].find(a =>
            /^\\s*(apply|apply now|apply for this job|apply on company site)\\s*$/i.test(a.innerText || '')
            && !/login|register|signup/i.test(a.href)); return a ? a.href : ''; }""")
    except Exception:
        href = ""
    if href:
        try:
            page.goto(href, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except Exception:
            pass
    return page.url


def _fetch_public(url: str, title: str, company: str, location: str):
    """Best-effort fetch of a job page without a browser (works for most ATS pages;
    Indeed/Glassdoor usually need the browser or a pasted description)."""
    import html as _html
    import re as _re
    import urllib.request
    final = url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) applyloop/1.0"})
        resp = urllib.request.urlopen(req, timeout=20)
        final = getattr(resp, "geturl", lambda: url)() or url
        raw = resp.read().decode("utf-8", "replace")
    except Exception:
        return "", title, company, location, final
    m = _re.search(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', raw, _re.S)
    if m:                                             # schema.org JobPosting (most ATSs)
        import json as _json
        try:
            data = _json.loads(m.group(1))
            data = next((d for d in (data if isinstance(data, list) else [data])
                         if d.get("@type") == "JobPosting"), data)
            title = title or data.get("title", "")
            org = data.get("hiringOrganization") or {}
            company = company or (org.get("name", "") if isinstance(org, dict) else "")
            loc = data.get("jobLocation") or {}
            loc = loc[0] if isinstance(loc, list) and loc else loc
            addr = loc.get("address", {}) if isinstance(loc, dict) else {}
            location = location or ", ".join(x for x in (addr.get("addressLocality"), addr.get("addressRegion")) if x)
            if data.get("jobLocationType") == "TELECOMMUTE":
                location = (location + " (Remote)").strip()
            desc = _re.sub(r"<[^>]+>", " ", _html.unescape(data.get("description", "")))
            if len(desc) > 200:
                return _re.sub(r"\s+", " ", desc).strip(), title, company, location, final
        except Exception:
            pass
    body = _re.sub(r"(?s)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    text = _re.sub(r"\s+", " ", _html.unescape(_re.sub(r"<[^>]+>", " ", body))).strip()
    if not title:
        t = _re.search(r"<title>(.*?)</title>", raw, _re.S)
        title = _html.unescape(t.group(1)).strip()[:120] if t else ""
    if _re.search(r"verify you are human|just a moment|enable javascript", text[:2000], _re.I):
        return "", title, company, location, final
    return (text if len(text) > 500 else ""), title, company, location, final
