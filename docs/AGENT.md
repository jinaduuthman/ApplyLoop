# applyloop agent — find, tailor, apply, log

```
hunt ─► Indeed / Glassdoor search (your logged-in agent browser) + company ATS APIs
     ─► dedupe (canonical URL + company/title/city fingerprint across sites)
     ─► free filters (title/company/clearance/salary floor)
     ─► route to a CV track (Data Eng · ML Eng · Research Scientist — whatever you configure)
     ─► score 0-5 (AI, or keyword fallback) ─► below track.min_score → low_score
     ─► CV: base .tex as-is, or tailored + recompiled (only surfaces skills you really have)
     ─► queued ─► review mode: apply now with you · queue mode: wait for approval
submit ─► Easy Apply (Indeed/Glassdoor) or company site ─► confirmation check ─► log
```

Everything is logged in `data/applyloop.db` (SQLite) and optionally mirrored to a Google Sheet.

## One-time setup (macOS / Linux)

```bash
pipx install applyloop          # or, from a clone: python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev,sheets]'
playwright install firefox      # the agent's own browser (works on Macs that block Chrome automation)
brew install tectonic           # optional: LaTeX engine for tailored .tex CVs (or MacTeX)
mkdir ~/job-search && cd ~/job-search
applyloop init                  # config/, .env, resumes/, data/, output/ from templates
# .env: an AI key (OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY) + free Adzuna keys
#       optional: BRAVE_API_KEY (employer search) and an IMAP app password (alert emails)
applyloop llm models --ai openai # confirm model IDs, set them in config/applyloop.yml → llm
applyloop llm test
# review config/applyloop.yml (tracks, search, filters) and config/answers.yml (every answer!)
applyloop doctor
```

## Daily use

| Goal | Command |
|---|---|
| Find + apply with me approving each submit | `applyloop hunt` (apply.mode = review) |
| Only Data Eng today, at most 5 applications | `applyloop hunt -t data_engineering --max-apply 5` |
| Prepare everything unattended, approve later | `applyloop hunt --mode queue` |
| See what's waiting | `applyloop queue list` · `applyloop queue show 42` |
| Approve / reject | `applyloop queue approve 42 43` · `--all --min-score 4` · `applyloop queue reject 44` |
| Submit approved | `applyloop submit` (auto only when every answer is trusted) |
| Submit with a final look at each | `applyloop submit --review` |
| I'll click apply myself | `applyloop submit 42 --manual` (opens your browser, logs the result) |
| Use my everyday Chrome instead | start Chrome with `--remote-debugging-port=9222 --user-data-dir=$HOME/.applyloop/cdp-profile`, then `--browser cdp` |
| What did I apply to? | `applyloop log` · `applyloop log --export applied.xlsx` |
| Recruiter replied / interview | `applyloop mark 42 interview -n "phone screen Tue"` |
| Funnel by track/source | `applyloop stats` |
| Questions it couldn't answer | `applyloop answers pending` → add rules to `config/answers.yml` |
| Preview tailoring for a JD | `applyloop cv --track ml_engineering --jd-file jd.txt` |
| Google Sheet | set `sheets.enabled: true` + id, then `applyloop sheets sync` (live updates afterwards) |

## AI provider and cost

Configured in `config/applyloop.yml → llm` (OpenAI, Anthropic, Gemini or local Ollama), with a
default model plus per-task overrides — e.g. a cheap model for scoring and a stronger one only
for CV rewrites (`tasks: {tailor: ...}`).

| Command | What |
|---|---|
| `applyloop llm show` | provider/model per task, key present, price used for estimates |
| `applyloop llm models --grep gpt` | model IDs your key can use |
| `applyloop llm test --task tailor` | one tiny call: latency, tokens, cost |
| `applyloop llm usage --since 2026-10-01` | calls, tokens and estimated $ by model/task |

Gemini: use a paid-tier key — Google may use free-tier content (your CV, contact details,
salary) to improve its products.

## Review vs queue (hybrid)

* **review** — the agent fills every step and stops at the final Submit with a table of
  every answer and its source (green = profile/answer bank, yellow = AI draft).
  You choose `s`ubmit, `m`anual (finish in the browser) or s`k`ip.
* **queue** — `hunt --mode queue` (or `--unattended`, e.g. from a schedule) prepares jobs
  and stops. After `queue approve`, `submit` sends applications automatically **only if**
  all answers came from your profile/answer bank and nothing required is blank. Anything
  else is parked as `needs_input` with the exact questions, or paused for you if you're there.

Switch per run with `--mode`, or set the default in `config/applyloop.yml → apply.mode`.

## CV selection and tailoring (.tex)

* Each track points at its own `.tex` (your Overleaf sources) — the router picks the track
  from the job title + JD keywords (AI breaks ties when it's ambiguous).
* `apply.tailor: auto` tailors only when the JD asks for skills that exist in **any** of your
  CVs but not in the chosen one, or keyword coverage is under 60%. Otherwise the base CV is
  used (compiled once and cached).
* Tailoring edits the original source line by line, so layout/macros are untouched:
  adds true-but-missing skills to the matching skills row, reorders bullets by relevance,
  and (with AI) rewrites the summary and up to 5 bullets.
* Guards: same numbers in every rewritten bullet; no skill you lack (e.g. JD wants Flink,
  none of your CVs mention it → reported as a gap, never written); length cap; LaTeX
  escaping; page limit (`max_pages`) — trims least-relevant older bullets or falls back to
  the base CV.
* Output: `output/cvs/<job>-<company>-<track>.{tex,pdf}`; notes in `applyloop queue show <id>`.

## Safety rails

* Never types passwords, creates accounts, solves CAPTCHAs or ticks terms/certification
  boxes — those come back to you. Workday/Taleo/iCIMS-style account portals → `manual`.
* EEO questions (gender, race, veteran, disability) → "decline to answer" option, never inferred.
* Daily cap (`apply.daily_cap`) and random 45–120 s pauses between submissions.
* Screenshots of the final review + confirmation in `output/applications/<id>/`.
* Job-page text is passed to the AI as untrusted data (prompt-injection guard).

## Scheduling (unattended queue runs)

`~/Library/LaunchAgents/com.applyloop.hunt.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.applyloop.hunt</string>
  <key>WorkingDirectory</key><string>/Users/YOU/path/to/applyloop</string>
  <key>ProgramArguments</key><array>
    <string>/Users/YOU/path/to/applyloop/.venv/bin/applyloop</string>
    <string>hunt</string><string>--unattended</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>5</integer></dict>
  <key>StandardOutPath</key><string>/tmp/applyloop-hunt.log</string>
  <key>StandardErrorPath</key><string>/tmp/applyloop-hunt.log</string>
</dict></plist>
```

`launchctl load ~/Library/LaunchAgents/com.applyloop.hunt.plist` — then each morning run
`applyloop queue list`, approve, `applyloop submit`.

## Making it work for other people / fields

Everything field-specific is config: tracks (titles, keywords, queries, CV file, page limit,
min score), filters, answer bank, sources. A nurse, PM or designer edits
`config/applyloop.yml` + `config/answers.yml` + `config/profile.yml`; no code changes.
Non-LaTeX users can point a track at a PDF (used as-is).

## Known limits

* Indeed, Glassdoor and LinkedIn show bot checks to automated browsers, so their jobs come in
  through your job-alert emails and are applied to on the employer's own site (or by you).
  applyloop never tries to get past a CAPTCHA or bot check.
* Teal's board and search results are read from public pages whose layout can change;
  `applyloop teal test` / `applyloop companies discover` show what is being picked up.
* Workday, Taleo and similar portals need your own account there, so those jobs are prepared
  (CV + answers) and handed to you.
