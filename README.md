# applyloop

A job-search agent you run from the terminal. It finds roles you target (Adzuna, Remotive, Teal,
company career sites, and your Indeed / Glassdoor / LinkedIn alert emails), filters and scores
each posting, picks the right CV for the role — tailoring and recompiling your **LaTeX** CV only
when it helps — applies on the employer's own application form, and logs every job.

```
hunt ─► search ─► dedupe ─► free filters (title, location, salary, age) ─► pick CV track
     ─► AI fit score ─► tailor .tex if needed ─► find where it really applies
     ─► apply (auto / review / queue) ─► log (SQLite + optional Sheet) ─► daily digest
```

Works for any field — roles, CVs, filters and answers all live in config files.

---

## 1. Install

**From PyPI** (macOS or Linux, Python 3.11+):

```bash
pipx install applyloop            # or: pip install applyloop   (extras: applyloop[sheets])
playwright install firefox
brew install tectonic             # optional: LaTeX engine for tailored CVs (or mactex-no-gui)
mkdir ~/job-search && cd ~/job-search
applyloop init                    # creates config/, .env, resumes/, data/, output/
```

Run `applyloop` from that folder, or set `APPLYLOOP_HOME=~/job-search` to run it from anywhere.

**From source** (to change the code):

```bash
git clone https://github.com/jinaduuthman/ApplyLoop.git && cd ApplyLoop
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,sheets]'
playwright install firefox
applyloop init                    # your files are gitignored, so they never get committed
```

The agent drives its **own Firefox profile** (`browser.engine: firefox`). On company-managed
Macs, Chrome automation is usually blocked by policy ("DevTools remote debugging is disallowed");
Firefox isn't affected. On a personal Mac you can use `engine: chromium` instead.
Without TeX the agent still works but uses your PDFs untailored.

## 2. What to set

| File | What to put there | Required |
|---|---|---|
| `.env` | AI key(s), Adzuna keys, alert-inbox app password (created by `applyloop init`) | AI / Adzuna / alerts |
| `config/profile.yml` | name, email, phone, links, location, target comp | yes |
| `config/applyloop.yml` | role tracks + CV files, search, filters, apply mode, browser, AI | yes |
| `config/answers.yml` | answers to screening questions (work auth, sponsorship, degree, school…) | yes — **review every line** |
| `config/eeo.yml` | voluntary self-ID answers (gender, race, veteran, disability) or `decline` | optional |
| `resumes/` | your `.tex` CVs (and PDF fallbacks) — working copies, originals untouched | yes |

`applyloop init` creates all of these from templates (it never overwrites a file you already have;
`--force` resets them). Then put your CV(s) in `resumes/` and point `tracks[].cv` at them.

### `.env`

```bash
OPENAI_API_KEY=sk-...          # any or all of these; the active one is chosen in applyloop.yml
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...             # use a paid key — free-tier content may be used by Google
ADZUNA_APP_ID=...              # free at developer.adzuna.com — main job search
ADZUNA_APP_KEY=...
APPLYLOOP_IMAP_USER=alerts.inbox@gmail.com   # inbox that receives Indeed/Glassdoor/LinkedIn alerts
APPLYLOOP_IMAP_PASSWORD=...                  # Gmail App Password (not your real password)
```

Set a monthly spend limit in your AI provider's dashboard. Never commit `.env`.

### `config/applyloop.yml` — the main settings

**Tracks** — one per kind of role, each with its own CV:

```yaml
cv_dir: resumes
tracks:
  - id: data_engineering
    cv: data_engineer.tex                     # tailored + recompiled when useful
    fallback_pdf: data_engineer.pdf           # used if TeX isn't installed
    max_pages: 1
    min_score: 3.5                            # don't queue below this fit (0–5)
    queries: ["Senior Data Engineer", "Data Engineer", "AI Engineer", "LLM Engineer"]   # what to search
    titles:  ["data engineer", "analytics engineer", "ai engineer", "llm engineer"]     # titles that belong to this track
    keywords: [spark, kafka, dbt, snowflake]             # JD words that signal this track
```

The AI & Data Engineer CV covers both data engineering and AI/LLM engineering roles (the
`data_engineering` track lists ~70 title variants: Snowflake/Databricks developer, ETL/data integration,
AI/ML, GenAI, LLM, agentic, "Software Engineer, AI", …).

**Pausing a track:** add `paused: true` to it. A paused track isn't searched, its titles stop matching,
and its queued / apply-yourself jobs wait (they keep their CV). `-t <id>` still runs it on demand; delete
the line to resume. ML Engineer / Data Scientist and Research tracks are paused right now.

**Remote first:** `search.prefer_remote: true` searches Remote before your city and applies to / walks
remote jobs first; local jobs still go out after them. `--remote` makes a run remote-only.

A job whose title matches no track's `titles` is dropped, so add every title variant you'd
take (e.g. "data scientist", "applied scientist", "ai/ml", "ml platform").

**Search & filters** (filters run before any AI call, so they're free):

```yaml
search:
  sources: [adzuna, remotive, alerts]   # also: indeed, glassdoor (browser — usually blocked by bot checks)
  location: "Austin, TX"
  remote: true                 # also search "Remote"
  posted_within_days: 7        # drop older postings
  allowed_locations: [Austin, Round Rock, Cedar Park]
                               # keep jobs located there OR remote; empty = anywhere
  remote_us_only: true         # drop "Remote – UK / India / Canada…"
  work_mode: any               # any | remote | hybrid | onsite | [remote, hybrid]
  remote_only: false           # true = every run remote-only (same as always passing --remote)
  max_per_query: 30
  resolve_links: true          # follow Adzuna links to see where each job really applies
  companies:                   # employers checked directly on every run (public job-board APIs)
    - {name: Stripe, ats: greenhouse, slug: stripe}
    - {name: Ramp, ats: ashby, slug: ramp}
    - {name: Palantir, ats: lever, slug: palantir}
    - {name: Example Corp, ats: workday, url: "https://example.wd5.myworkdayjobs.com/Careers"}
filters:
  title_exclude: [intern, junior, manager, staff, principal, distinguished, director]
  company_blocklist: []
  description_exclude: ["security clearance", "u\\.?s\\.? citizens? only"]
  min_salary: 100000           # jobs with no salary listed are kept
  exclude_staffing: true       # drop staffing agencies (known firms, "…Staffing/Recruiting" names,
                               # "our client" / C2C / W2-only postings)
  exclude_clearance: true      # drop jobs needing a security clearance — Secret, Top Secret, TS/SCI,
                               # polygraph, Public Trust, "able to obtain a clearance" (title or
                               # description). "No clearance required" is kept. false = allow them.
  staffing_firms_extra: []     # more agency names to drop
  exclude_w2: true             # drop postings mentioning "W2" (agency contracts, usually hourly, no benefits)
```

Jobs listed only as "United States" or with no location are kept for scoring (can't tell where).
`applyloop refilter` re-applies changed filters to everything not yet applied to.

**Applying:**

```yaml
apply:
  mode: auto                   # auto = unattended · review = you approve each submit · queue = approve in batches
  daily_cap: 20
  delay_seconds: [3, 8]        # random pause between applications
  email_code_wait_minutes: 15  # Greenhouse "enter the code we emailed you" — waits for YOU to type it
  cover_letter: required       # only when the form makes it mandatory (auto | always | never)
  tailor: auto                 # auto | always | never
  auto:
    min_score: 4.0             # auto-apply only to strong matches; the rest wait in the queue
    max_per_run: 10
    allow_ai_answers: false    # true = also submit AI-drafted answers unseen
    headless: false
    track_rotation:            # which track gets first pick each day (others fill the rest of the cap)
      mon: data_engineering
      tue: ml_engineering
      wed: research_scientist
      default: data_engineering
    rotation_only: false       # true = search and apply ONLY for that day's track
```

**AI providers** — keep all three configured, switch any time:

```yaml
llm:
  use: openai                  # openai | anthropic | gemini | ollama | auto
  reasoning_effort: low
  providers:
    openai:    {model: gpt-5.6-luna, tasks: {tailor: gpt-5.6-terra}}
    anthropic: {model: claude-haiku-4-5-20251001, tasks: {tailor: claude-sonnet-5-5}}
    gemini:    {model: gemini-3.1-flash-lite, tasks: {tailor: gemini-3.8-flash}}
  prices: {gpt-5.6-luna: [0.20, 1.20], ...}   # $/1M tokens, for spend estimates
```

Each provider has a cheap default model (scoring, routing, form answers) and a stronger one only
for CV rewrites. Confirm model IDs with `applyloop llm models --ai <provider>`.

**Browser:**

```yaml
browser:
  engine: firefox  # firefox | chromium
  mode: profile    # profile = agent's own browser profile · manual = never drives a browser
```

### `config/answers.yml` — how screening questions get answered

Order: profile.yml → your rules in answers.yml → eeo.yml → AI (AI answers are never
auto-submitted unless `allow_ai_answers: true`).

```yaml
defaults:
  current_title:               # per track (matches the CV that's sent)
    data_engineering: "Senior Data Engineer"
    default: "Senior Engineer"
  highest_degree:              # a list = alternatives, tried against the form's options
    ml_engineering: ["Doctor of Philosophy (Ph.D.)", "PhD", "Doctorate"]
    default: ["Master's Degree", "Master's"]
  past_employers: [Acme Corp, Globex]   # "ever worked for X?" → Yes only for these
rules:
  - match: ["require (visa )?sponsorship"]     # regexes, matched as whole words
    answer: "No"
  - match: ["currently reside", "countr(y|ies) of residence"]
    answer: ["United States", "United States of America", "USA", "US"]   # first one offered
  - match: ["^degree$", "highest (level of )?education"]
    answer: "{highest_degree}"
```

Your file also covers school, start date, city, "how did you hear", WhatsApp opt-in, and an
**acknowledgement rule** (privacy notice / "information is true" / terms) — delete that rule if
you'd rather tick those yourself. Test any question: `applyloop answers test "Degree" -o PhD -o Masters`.

## 3. First run

```bash
applyloop doctor                    # checks Python, browser, TeX, CV files, keys, configs
applyloop llm test                  # one tiny AI call (latency, tokens, cost)
applyloop companies check           # are the company boards in your list reachable?
applyloop alerts test               # (if using alert emails) what the parser finds
applyloop hunt --mode review --days 3 --max-apply 3   # small watched run
```

## 4. Everyday commands

### Find and apply

| Do this | Command |
|---|---|
| Full run with your configured mode | `applyloop hunt` |
| Only jobs from the last 3 days | `applyloop hunt --days 3` |
| One track / fewer searches | `applyloop hunt -t ml_engineering --max-queries 2` |
| Your area + remote (default) | `applyloop hunt --days 3` |
| Remote only this run (search + apply) | `applyloop hunt --days 3 --remote` |
| Remote data engineering only | `applyloop hunt --days 3 --remote -t data_engineering` |
| …same, without any prompts | `applyloop hunt --days 3 --remote -t data_engineering --unattended` |
| Walk only jobs posted in the last 3 days | `applyloop submit --manual --max-age 3` |
| Walk your apply-yourself list, remote DE only | `applyloop submit --manual --remote -t data_engineering` |
| Submit approved jobs, remote only | `applyloop submit --remote` |
| Prepare everything now, decide later | `applyloop hunt --mode queue` |
| Unattended run (never prompts) | `applyloop hunt --unattended` |
| Only one kind of role today (search + apply) | `applyloop hunt -t data_engineering --unattended` |
| Overnight on a laptop (keeps the Mac awake) | `caffeinate -i applyloop hunt --unattended` |
| Find employers hiring on Greenhouse/Lever/Ashby/Workable/YC | `applyloop companies discover "Data Engineer"` |
| Check what Teal returns for a title | `applyloop teal test "Data Engineer"` |
| Use another AI provider this run | `applyloop hunt --ai anthropic` |
| Add a job I found myself (any site) | `applyloop add <url>` · Indeed/Glassdoor: `applyloop add <url> --paste` |

**Employers you haven't listed.** With `search.ats_search.enabled` and a `BRAVE_API_KEY` (or
`SERPAPI_KEY`) in `.env`, each hunt runs searches like `site:jobs.lever.co "Data Engineer"` (past week)
on Greenhouse, Lever, Ashby, Workable and YC. Every employer board found is read through its job API
like your configured companies (so most can be auto-applied) and remembered for 30 days in
`data/discovered_companies.json`.

**Fresh jobs only.** Ages count from the employer's posting date (or when applyloop found the job,
if no date is known). `submit --manual --max-age 3` shows only jobs posted in the last 3 days; older
ones stay on your list for another time (no flag = all). Auto-apply skips queued jobs older than
`apply.max_job_age_days` (default 7) and leaves them queued.

**Remote vs local.** Without a flag, jobs in `allowed_locations` (your metro area) *and* remote US jobs
are found and applied to. `--remote` (alias `--remote-only`) limits that run — both the search and the
applications — to remote jobs; it works on `hunt` and `submit` (incl. `--manual`) and combines with
`-t <track>`. Company job boards still return every location, so local jobs found during a `--remote`
run are scored and left queued (not applied, not dropped) for your next run without the flag.

### Queue (review, then submit)

| Do this | Command |
|---|---|
| See what's waiting (with **Apply at** and **Who**: auto / you / ?) | `applyloop queue list` |
| Only jobs applyloop can apply to on its own | `applyloop queue list --who auto` |
| Find where Adzuna / listing-site jobs really apply | `applyloop resolve` · `--limit 10` · `--retry` (also re-checks ones handed to you) |
| Everything about one job | `applyloop queue show 42` |
| Approve | `applyloop queue approve 42 43` · `--all --min-score 4` |
| Reject | `applyloop queue reject 44` |
| Submit approved jobs | `applyloop submit` · `applyloop submit 42 43 --unattended` |
| Pause at each final step | `applyloop submit --review` |
| I'll apply myself (opens my browser, logs the result) | `applyloop submit 42 --manual` |
| Found the employer's own link for a job (instead of Glassdoor/LinkedIn) | `applyloop queue link 42 <url>` |
| Go through my whole apply-yourself list, one after another | `applyloop submit --manual` · `-t data_engineering` · `--limit 10` |
| Re-apply current filters to the queue | `applyloop refilter` |

A job set back to `needs_input` must be approved again before `submit` sends it.
`submit` auto-sends only when every answer came from profile / answers.yml / eeo.yml and
nothing required is blank; otherwise the job is marked `needs_input` with the exact questions.

### Log and follow-up

| Do this | Command |
|---|---|
| What I applied to | `applyloop log` · export: `applyloop log --export applied.xlsx` |
| Record a reply / interview / offer / rejection | `applyloop mark 42 interview -n "phone screen Tue"` |
| Funnel by status, track, source | `applyloop stats` |
| Questions it couldn't answer → add to answers.yml | `applyloop answers pending` |
| Google Sheet mirror (after setup below) | `applyloop sheets sync` |

### CVs

| Do this | Command |
|---|---|
| Which CV a job got and what changed | `applyloop cv 42` |
| Preview tailoring against a JD | `applyloop cv --track research_scientist --jd-file jd.txt` |

Tailored CVs: `output/cvs/<job>-<company>/JaneSmith_DE.pdf` (+ `.tex`); base CVs: `output/cvs/JaneSmith_DE_Base.pdf`,
`…_MLE_Base.pdf`, `…_ResearchScientist_Base.pdf`. The name comes from profile.yml; set a track's tag with `short:` in applyloop.yml.
The agent only surfaces skills that already appear in one of your CVs; skills a JD wants that
you don't have are reported as gaps and never added. Rewritten bullets keep the same numbers,
and the page limit is enforced.

### AI provider and spend

| Do this | Command |
|---|---|
| Switch default provider | `applyloop llm use gemini` |
| Models available to my key | `applyloop llm models --ai openai --grep gpt` |
| Tokens and estimated $ | `applyloop llm usage --since 2026-10-01` |

No key at all? Everything still runs with keyword scoring and non-AI tailoring.

## 5. How automatic is it?

Each `hunt` (in `auto` mode): search → filter → score → pick/tailor CV → resolve Adzuna links →
**auto-submit** strong matches on forms that need no login → log → digest in
`output/digests/<date>.md` + a macOS notification.

| Where the job applies | What happens |
|---|---|
| Greenhouse, Lever, Ashby, SmartRecruiters, Workable, BambooHR, simple careers pages | filled and submitted by applyloop |
| Greenhouse asking "enter the code we emailed you" | applyloop fills everything, then **waits for you** to type the code (it's a human check) — notification + window brought to front; parked as `needs_input` if nobody does |
| Glassdoor / Indeed / LinkedIn (incl. Easy Apply) | applyloop looks for the same job on the employer's own site and applies there; if not found → opened in your browser / listed for you |
| Workday, Taleo, iCIMS… (account portals) | CV prepared; you apply with your account |
| Listing sites (Adzuna, Jooble, Diversity.com, Dice…) | never typed into; applyloop follows the Apply link, then looks for the job on the employer's own board (your company list, then Greenhouse/Lever/Ashby by name); found → back in the auto queue, otherwise handed to you |
| Posting closed ("no longer available", "position filled") | marked `expired`, not handed to you |
| A required question your answers don't cover | `needs_input` → `applyloop answers pending`, add a rule, approve + submit again |

```bash
applyloop schedule install --time 08:05 --weekdays   # every weekday morning, unattended
applyloop schedule status
```

Adding the questions from `answers pending` to `answers.yml` is what drives interventions
toward zero. In practice most automatic applications come from `search.companies` (employer
boards on Greenhouse/Ashby/Lever); many Adzuna results are reposts on other listing sites, often
already closed, or big employers on Workday.

### Indeed / Glassdoor / LinkedIn jobs (via job-alert emails)

Those sites block automated browsers, so applyloop uses the channel they offer: **alert emails**.
Use a separate inbox so the alerts don't clutter your main email.

1. On Indeed, Glassdoor and LinkedIn (your normal browser), create daily alerts for your target titles, sent to that inbox.
2. Gmail: turn on 2-Step Verification → create an **App Password**.
3. `.env`: `APPLYLOOP_IMAP_USER` / `APPLYLOOP_IMAP_PASSWORD`.
4. Add `alerts` to `search.sources`. Check with `applyloop alerts test`.

The mailbox is opened read-only.

## 6. Optional setup

**Google Sheets mirror** — create a Google Cloud service account + JSON key, save it to
`~/.applyloop/google-service-account.json`, share your Sheet with the service account's email,
then in `applyloop.yml`: `sheets: {enabled: true, spreadsheet_id: "<id from the sheet URL>"}`.

**More company boards** — add `{name, ats, slug}` (Greenhouse/Lever/Ashby) or
`{name, ats: workday, url}` to `search.companies`, then `applyloop companies check`.

## 7. What the agent will never do

Type passwords, create accounts, solve CAPTCHAs or get past bot checks, enter email
verification codes for you, type your details into job-listing sites, invent experience, or
guess EEO answers. Acknowledgement boxes are ticked only by your own answers.yml rule.
It respects the daily cap and pauses between submissions; screenshots of each final review and
confirmation are kept in `output/applications/<id>/`.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Chrome won't start / "remote debugging is disallowed" | managed Mac — use `browser.engine: firefox` |
| Firefox exits immediately (exit code 0) | a stale window holds the profile: `pkill -f firefox-profile`, run again |
| Verification / Cloudflare page on Indeed/Glassdoor | expected — use alert emails, Adzuna, company boards, or `applyloop add <url> --paste` |
| Queue shows `?` under Who | `applyloop resolve` (Adzuna links not followed yet) |
| Adzuna link "didn't lead to an employer page" | see `output/applications/<id>/resolve.png`; `applyloop resolve --retry` |
| `submit clicked but no confirmation seen` | check the screenshot in `output/applications/<id>/`; often an email code was asked |
| Many jobs `needs_input` | `applyloop answers pending` → add rules to `answers.yml` |
| Company slug wrong | `applyloop companies check` |
| CV not tailored | `applyloop doctor` (TeX installed?) · `applyloop cv <id>` shows why |
| AI errors / wrong model ID | `applyloop llm models --ai <provider>` → fix IDs → `applyloop llm test` |
| Too expensive | cheaper `model`, fewer `queries`, stricter filters; `applyloop llm usage` |

## 9. Files

```
config/        applyloop.yml · profile.yml · answers.yml · eeo.yml (templates ship inside the package)
resumes/       your .tex / .pdf CVs (working copies)
data/          applyloop.db (all jobs, status history, questions, AI usage)
output/        cvs/ · applications/<id>/ (screenshots) · digests/ · cover-letters/
docs/AGENT.md  deeper reference (design, safety rails, scheduling)
```

Run `pytest` to run the test suite.

## 10. Use it as a library

```python
from applyloop import Agent, load_settings, filter_reason, AnswerBank, TexCV

agent = Agent(interactive=False)                 # reads ./config/applyloop.yml (or $APPLYLOOP_HOME)
summary = agent.hunt(mode="queue")               # find + filter + score + prepare CVs; submits nothing
print(summary.found, summary.queued)

s = load_settings()
filter_reason({"title": "Senior Data Engineer", "location": "Remote"}, s.filters, s.tracks, s.search)
# → None (kept) or a reason string, e.g. "security clearance ('TS/SCI')"
```

Also exported: `Store` (the SQLite job log), `route` (pick a CV track), `detect_work_mode`,
`init_project`, `ProjectPaths`, `Settings`, `Track`, `JobPosting`.

**Upgrading from `jobpilot`:** old names keep working — `config/jobpilot.yml`, `data/jobpilot.db`,
`~/.jobpilot/` browser profiles and `JOBPILOT_*` variables are picked up when the new ones don't exist.
Run `applyloop schedule install` again to replace an old daily schedule.

<details>
<summary>Older single-job pipeline commands (still available)</summary>

`applyloop run <url>` · `evaluate <url>` · `tailor <url>` · `pdf <in.html> <out.pdf>` ·
`find` · `scan` · `apply <url> --resume cv.pdf` · `apply-batch <file>` · `autopilot` ·
`tracker merge|normalize|dedup` · `analytics` · `export csv|json|excel` · `keywords --file jd.txt` ·
`remind list|check|auto` · `verify` · `sync-check` · `liveness <urls>` · `dashboard`.
These work on `data/applications.md`; the agent commands above use `data/applyloop.db`.
</details>

Changes are listed in `CHANGELOG.md`; see `CONTRIBUTING.md` before opening a pull request.

## 11. Credits & license

MIT licensed (see `LICENSE`). applyloop began as a Python port of
[career-ops](https://github.com/santifer/career-ops) by Santiago Fernández de Valderrama (MIT),
and has since been rebuilt as an autonomous search-and-apply agent.

Use it responsibly: review your answers file, respect each site's terms, and keep the daily cap sensible.
