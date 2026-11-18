"""Indeed / Glassdoor / LinkedIn jobs via the job-alert emails those sites send you.

This is the sanctioned way to get their listings into an automated pipeline: you set up job
alerts on the sites (in your normal browser), they email you matches, and applyloop reads
those emails from your mailbox over IMAP — read-only, messages are not marked as read.

Setup (Gmail):
  1. Turn on 2-Step Verification, then create an App Password (myaccount.google.com → Security
     → App passwords). applyloop never sees your real Google password.
  2. .env:  APPLYLOOP_IMAP_USER=you@gmail.com   APPLYLOOP_IMAP_PASSWORD=<16-char app password>
  3. config/applyloop.yml: add `alerts` to search.sources (and see `email_alerts:`).
  4. On Indeed / Glassdoor / LinkedIn, create daily alerts for your target titles.

For each alert job, applyloop then looks for the same job on the company's own site (via Adzuna
and your configured company boards). If it finds it, it can apply there automatically; if not,
the job is queued with a tailored CV and listed in the digest for you to apply on the board.
"""

from __future__ import annotations

import email
import email.policy
import html as _html
import imaplib
import os
import re
from datetime import date, timedelta

from . import JobPosting

BOARD_DOMAINS = {"indeed.com": "indeed", "glassdoor.com": "glassdoor", "linkedin.com": "linkedin",
                 "tealhq.com": "teal"}
JOB_LINK = re.compile(r"indeed\.com/(rc/clk|viewjob|pagead|m/viewjob|applystart|jobs)|jk=|"
                      r"glassdoor\.com/.*(job-listing|jobListingId|partner/jobListing|Job/)|"
                      r"linkedin\.com/(comm/)?jobs/view|tealhq\.com/job/", re.I)
NOISE = re.compile(r"^(view( all)?( jobs?)?|apply( now)?|see (all|more).*|unsubscribe|easily apply|"
                   r"new|save|more jobs.*|manage alerts?|edit.*alert.*|indeed|glassdoor|linkedin)$", re.I)


def configured() -> bool:
    return bool(os.environ.get("APPLYLOOP_IMAP_USER") and os.environ.get("APPLYLOOP_IMAP_PASSWORD"))


def _html_with_links(html: str) -> str:
    """HTML → text where every anchor becomes `[text](href)` (keeps job links + surroundings)."""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
               lambda m: f" [{re.sub(r'<[^>]+>', ' ', m.group(2)).strip()}]({_html.unescape(m.group(1))}) ", s)
    s = re.sub(r"(?i)<(br|/p|/div|/tr|/td|/li|/h\d)[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t ]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


_INVISIBLE = "\u200b\u200c\u200d\u200e\u200f\ufeff\u034f\u00a0"
_RATING = re.compile(r"^\d\.\d\s*★?$")
_AGE = re.compile(r"^\d+\s*[dhw]\+?$|^(just posted|today|new)$", re.I)


def _parts(inner_html: str) -> list[str]:
    """The text pieces of an element, one per tag, in order."""
    inner = re.sub(r"(?is)<!--.*?-->|<style.*?</style>|<script.*?</script>", "", inner_html)
    out = []
    for x in re.split(r"<[^>]+>", inner):
        t = _html.unescape(re.sub(r"\s+", " ", x)).strip(" " + _INVISIBLE)
        if t:
            out.append(t)
    return out


def parse_glassdoor(html: str) -> list[JobPosting]:
    """Glassdoor alerts: each job is one link whose pieces are
    [company, rating★?, title, location, salary?, (est.)?, badges…, age]."""
    jobs, seen = [], set()
    for m in re.finditer(r'(?is)<a\b[^>]*href=["\']([^"\']*(?:jobListing|job-listing)[^"\']*)["\'][^>]*>(.*?)</a>', html):
        url = _html.unescape(m.group(1))
        p = _parts(m.group(2))
        if len(p) < 3:
            continue
        jid = re.search(r"jobListingId=(\d+)|jl=(\d+)", url)
        key = (jid.group(1) or jid.group(2)) if jid else url.split("&")[0]
        if key in seen:
            continue
        seen.add(key)
        company, i = p[0], 1
        if i < len(p) and _RATING.match(p[i]):
            i += 1
        if i + 1 > len(p):
            continue
        title = p[i]
        location = p[i + 1] if i + 1 < len(p) and not p[i + 1].startswith("$") and not _AGE.match(p[i + 1]) else ""
        salary = next((x for x in p if x.startswith("$")), "")
        posted = next((x for x in reversed(p) if _AGE.match(x)), "")
        jobs.append(JobPosting(url=url, title=title, company=company, location=location, salary=salary,
                               posted=posted, source="glassdoor-alert", easy_apply="Easy Apply" in p,
                               extra={"alert": True}))
    return jobs


def parse_linkedin(html: str) -> list[JobPosting]:
    """LinkedIn alerts: title link, then 'Company · City, ST (Remote|Hybrid|On-site)'."""
    text = _html_with_links(html)
    jobs, seen = [], set()
    for m in re.finditer(r"\[([^\]]{3,160})\]\((https?://[^)\s]*linkedin\.com/(?:comm/)?jobs/view/(\d+)[^)\s]*)\)", text):
        title, url, jid = " ".join(m.group(1).split()), m.group(2), m.group(3)
        if jid in seen or NOISE.match(title):
            continue
        seen.add(jid)
        after = re.sub(r"\[[^\]]*\]\([^)]*\)", "\n", text[m.end(): m.end() + 400])
        lines = [l.strip(" " + _INVISIBLE) for l in after.split("\n") if l.strip(" " + _INVISIBLE)]
        company = location = salary = ""
        for l in lines[:3]:
            if " · " in l and not company:
                company, _, location = l.partition(" · ")
            elif "$" in l and not salary:
                salary = l
        jobs.append(JobPosting(url=f"https://www.linkedin.com/jobs/view/{jid}/", title=title,
                               company=company.strip(), location=location.strip(), salary=salary,
                               source="linkedin-alert", extra={"alert": True}))
    return jobs


def parse_alert(html: str, board: str) -> list[JobPosting]:
    if "confirmemail" in html and "indeedapply" in html:
        return []                    # Indeed "your application was sent" receipts, not job alerts
    if board == "glassdoor":
        jobs = parse_glassdoor(html)
        if jobs:
            return jobs
    if board == "linkedin":
        jobs = parse_linkedin(html)
        if jobs:
            return jobs
    return _parse_generic(html, board)


def _parse_generic(html: str, board: str) -> list[JobPosting]:
    """Heuristic parser: each job link's text is the title; the next 1–2 short lines are
    company and location; the following line (if long) is a snippet."""
    text = _html_with_links(html)
    jobs: list[JobPosting] = []
    seen: set[str] = set()
    for m in re.finditer(r"\[([^\]]{3,160})\]\((https?://[^)\s]+)\)", text):
        title, url = " ".join(m.group(1).split()), m.group(2)
        if not JOB_LINK.search(url) or NOISE.match(title) or len(title) < 4 or len(title.split()) > 12 \
                or not re.search(r"[A-Za-z]{3}", title):
            continue
        key = url.split("&")[0]
        if key in seen:
            continue
        seen.add(key)
        after = text[m.end(): m.end() + 600]
        after = re.sub(r"\[[^\]]*\]\([^)]*\)", "\n", after)           # drop following links
        lines = [l.strip(" -·|•") for l in after.split("\n") if l.strip(" -·|•")]
        lines = [l for l in lines if not NOISE.match(l)]
        company = lines[0] if lines and len(lines[0]) < 80 else ""
        location = lines[1] if len(lines) > 1 and len(lines[1]) < 80 else ""
        snippet = " ".join(l for l in lines[2:4] if len(l) > 40)
        salary = next((l for l in lines[:5] if "$" in l and len(l) < 60), "")
        if location == salary:
            location = ""
        jobs.append(JobPosting(url=url, title=title, company=company, location=location,
                               salary=salary, source=f"{board}-alert", description=snippet,
                               extra={"alert": True}))
    return jobs


def fetch(host: str = "imap.gmail.com", folder: str = "INBOX", lookback_days: int = 3,
          senders: list[str] | None = None, max_messages: int = 60, dump_dir=None) -> list[JobPosting]:
    if not configured():
        raise RuntimeError("Add APPLYLOOP_IMAP_USER and APPLYLOOP_IMAP_PASSWORD (an app password) to .env")
    senders = senders or list(BOARD_DOMAINS)
    since = (date.today() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
    out: list[JobPosting] = []
    imap = imaplib.IMAP4_SSL(host)
    try:
        # Google shows app passwords as "abcd efgh ijkl mnop"; the spaces aren't part of it
        imap.login(os.environ["APPLYLOOP_IMAP_USER"].strip(),
                   re.sub(r"\s+", "", os.environ["APPLYLOOP_IMAP_PASSWORD"]).strip("'\""))
        mailbox = f'"{folder}"' if " " in folder and not folder.startswith('"') else folder
        typ, _ = imap.select(mailbox, readonly=True)             # never changes your mailbox
        if typ != "OK":
            _, boxes = imap.list()
            names = [b.decode(errors="replace").split(' "/" ')[-1].strip('"') for b in boxes or []]
            raise RuntimeError(f"mailbox/label '{folder}' not found — set email_alerts.folder to one of: "
                               + ", ".join(n for n in names if not n.startswith("[Gmail]"))[:400])
        for sender in senders:
            typ, data = imap.search(None, "SINCE", since, "FROM", f'"{sender}"')
            if typ != "OK":
                continue
            ids = data[0].split()[-max_messages:]
            for mid in ids:
                typ, msg_data = imap.fetch(mid, "(BODY.PEEK[])")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                msg = email.message_from_bytes(msg_data[0][1], policy=email.policy.default)
                part = msg.get_body(preferencelist=("html",))
                if part is None:
                    continue
                board = next((b for d, b in BOARD_DOMAINS.items() if d in sender), "board")
                html = part.get_content()
                if dump_dir:                                     # for tuning the parser
                    from pathlib import Path
                    d = Path(dump_dir)
                    d.mkdir(parents=True, exist_ok=True)
                    (d / f"{board}-{mid.decode()}.html").write_text(html, encoding="utf-8")
                out.extend(parse_alert(html, board))
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return out
