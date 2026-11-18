"""Mirror the SQLite log to a Google Sheet (SQLite stays the source of truth).

Setup (one time):
  1. Google Cloud console → create a service account → create a JSON key.
     Save it to ~/.applyloop/google-service-account.json (chmod 600).
  2. Create a Google Sheet, share it (Editor) with the service account's e-mail.
  3. Put the sheet id (the long string in its URL) in config/applyloop.yml → sheets.
  4. pip install gspread   ·   applyloop sheets sync
"""

from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console

console = Console()

COLUMNS = ["ID", "Status", "Applied At", "Company", "Title", "Location", "Salary", "Source",
           "Easy Apply", "Track", "Score", "Tailored CV", "CV File", "Method", "Detail", "URL",
           "Found At"]


def row_values(r) -> list:
    return [r["id"], r["status"], r["applied_at"] or "", r["company"] or "", r["title"] or "",
            r["location"] or "", r["salary"] or "", r["source"] or "",
            "yes" if r["easy_apply"] else "no", r["track"] or "",
            r["score"] if r["score"] is not None else "", "yes" if r["cv_tailored"] else "no",
            Path(r["cv_pdf"]).name if r["cv_pdf"] else "", r["apply_method"] or "",
            (r["status_detail"] or "")[:300], r["url"] or "", r["found_at"] or ""]


class SheetSync:
    def __init__(self, cfg: dict):
        import gspread   # optional dependency

        key = Path(cfg.get("service_account_json", "")).expanduser()
        if not key.exists():
            raise FileNotFoundError(f"Service-account key not found: {key}")
        gc = gspread.service_account(filename=str(key))
        sh = gc.open_by_key(cfg["spreadsheet_id"])
        name = cfg.get("worksheet", "Applications")
        try:
            self.ws = sh.worksheet(name)
        except gspread.WorksheetNotFound:
            self.ws = sh.add_worksheet(name, rows=1000, cols=len(COLUMNS))
        if self.ws.row_values(1) != COLUMNS:
            self.ws.update(range_name="A1", values=[COLUMNS])
            self.ws.freeze(rows=1)
        self._index: dict[str, int] | None = None
        self._last = 0.0

    def _rows(self) -> dict[str, int]:
        if self._index is None:
            ids = self.ws.col_values(1)[1:]
            self._index = {v: i + 2 for i, v in enumerate(ids) if v}
        return self._index

    def upsert(self, r) -> None:
        # only mirror jobs that got somewhere — keeps the sheet readable
        if r["status"] in ("found", "filtered", "low_score", "expired"):
            return
        gap = time.time() - self._last
        if gap < 1.1:                       # stay under Sheets' per-user write quota
            time.sleep(1.1 - gap)
        idx = self._rows()
        vals = row_values(r)
        key = str(r["id"])
        if key in idx:
            self.ws.update(range_name=f"A{idx[key]}", values=[vals])
        else:
            self.ws.append_row(vals, value_input_option="USER_ENTERED")
            idx[key] = len(idx) + 2
        self._last = time.time()

    def sync_all(self, rows) -> int:
        keep = [r for r in rows if r["status"] not in ("found", "filtered", "low_score", "expired")]
        values = [COLUMNS] + [row_values(r) for r in keep]
        self.ws.clear()
        self.ws.update(range_name="A1", values=values)
        self._index = None
        return len(keep)


def attach(store, cfg: dict) -> SheetSync | None:
    """Register live sync on the store if enabled; failures never break the agent."""
    if not cfg.get("enabled") or not cfg.get("spreadsheet_id"):
        return None
    try:
        sync = SheetSync(cfg)
    except Exception as e:
        console.print(f"[yellow]Google Sheets sync disabled: {e}[/yellow]")
        return None
    store.listeners.append(sync.upsert)
    return sync
