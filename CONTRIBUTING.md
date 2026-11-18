# Contributing

Thanks for helping. A few ground rules keep applyloop safe and useful for everyone:

1. **Never commit personal data.** Your `.env`, `config/*.yml` (except templates in
   `applyloop/starter/`), `resumes/`, `data/` and `output/` are gitignored — keep it that way.
   Use made-up names (Jane Smith, Acme Corp, `555-…` numbers) in tests and docs.
2. **No bot-check or CAPTCHA evasion, no password handling.** applyloop hands those steps to
   the person. Pull requests that try to get around them won't be merged.
3. **Filters stay free.** Anything that can be decided from the title, company, location or
   description should be a filter in `applyloop/filters.py`, run before any AI call.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,sheets]'
pytest -q --ignore=tests/test_apply_e2e.py     # the e2e tests need `playwright install firefox`
```

New job sources go in `applyloop/sources/` and return `JobPosting` objects; add a parser test
with a small HTML/JSON sample (see `tests/test_agent_core.py::test_teal_listing_parser`).
