# Changelog

## 0.1.0 — first public release

- `applyloop init` sets up a working folder from packaged templates (config, `.env`, `resumes/`).
- Sources: Adzuna, Remotive, Teal, job-alert emails (Indeed / Glassdoor / LinkedIn / Teal),
  company boards on Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee and Workday,
  plus employer discovery through a search API (`search.ats_search`).
- Filters before any AI call: title, age, pay, location (with "remote but really abroad"
  detection), staffing agencies / W2 / C2C, security clearance, duplicates and reposts
  (including company-name variants).
- CV tracks with optional pausing (`paused: true`, or `-t <track>` for one run); LaTeX tailoring
  only when the base CV misses skills you have; CVs rebuilt automatically when the base changes.
- Applying: auto / review / queue modes, daily and per-company caps, email-code wait,
  apply-yourself walk with `--max-age` and `--remote`.
- Usable as a library: `from applyloop import Agent, load_settings, filter_reason, ...`.
- Upgrading from `jobpilot`: old config, database, browser profile and `JOBPILOT_*` names still work.
