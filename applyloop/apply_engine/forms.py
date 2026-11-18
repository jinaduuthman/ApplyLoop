"""Generic, step-by-step application form filler.

Works on Indeed/Glassdoor Easy Apply (smartapply.indeed.com) and on most company
ATS forms (Greenhouse, Lever, Ashby, SmartRecruiters, iCIMS, generic) because it
reads fields the way a person does — by their visible label — instead of relying
on per-site CSS selectors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..answers import Answer, AnswerBank

COLLECT_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return (r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none') || el.type === 'file'; };
  const clean = t => (t || '').replace(/\s+/g, ' ').replace(/\*+\s*$/, '').trim();
  const labelFor = el => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(' ').map(id => (document.getElementById(id) || {}).innerText || '').join(' '); if (t.trim()) return t; }
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText; }
    const wrap = el.closest('label'); if (wrap) return wrap.innerText;
    const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) return lg.innerText; }
    let n = el.closest('div, li, section');
    for (let i = 0; n && i < 4; i++, n = n.parentElement) {
      const l = n.querySelector('label, legend, [class*="label" i], [class*="question" i], h3, h4');
      if (l && l.innerText.trim() && !l.contains(el)) return l.innerText;
    }
    return el.placeholder || el.name || '';
  };
  document.querySelectorAll('[data-jp]').forEach(e => e.removeAttribute('data-jp'));
  document.querySelectorAll('[data-jp-grouped]').forEach(e => e.removeAttribute('data-jp-grouped'));
  const out = []; let k = 0; const seenRadio = new Set();
  // Yes/No toggle buttons (Ashby & co.): a container with exactly the buttons Yes and No
  const ynBoxes = new Set();
  document.querySelectorAll('button').forEach(b => {
    const box = b.parentElement; if (!box || ynBoxes.has(box)) return;
    const bs = [...box.querySelectorAll(':scope > button')];
    const txt = bs.map(x => (x.innerText || '').trim().toLowerCase()).sort().join('|');
    const shown = e => { const st = getComputedStyle(e); return st.display !== 'none' && st.visibility !== 'hidden' && !!e.offsetParent; };
    if (bs.length === 2 && txt === 'no|yes' && shown(box)) ynBoxes.add(box);
  });
  for (const box of ynBoxes) {
    const hidden = box.querySelector('input');
    const entry = box.closest('[class*="field-entry" i], [class*="fieldEntry" i], [class*="question" i]') || box.parentElement;
    let lab = hidden ? labelFor(hidden) : '';
    if (!lab || /^(yes|no)$/i.test(lab.trim())) { const l = entry && entry.querySelector('label, legend, [class*="title" i]'); lab = l ? l.innerText : ''; }
    const key = hidden && (hidden.id || hidden.name);
    const labEl = (key && document.querySelector(`label[for="${CSS.escape(key)}"]`)) || (entry && entry.querySelector('label'));
    if (labEl && (!lab || /^(yes|no)$/i.test(lab.trim()))) lab = labEl.innerText;
    const id = 'jp' + (k++); box.querySelectorAll(':scope > button').forEach(b => b.setAttribute('data-jp', id));
    if (hidden) hidden.setAttribute('data-jp-yn', '1');
    out.push({id, type: 'yesno', label: clean(lab), name: '', options: ['Yes', 'No'],
              required: !!(labEl && /required/i.test(labEl.className)) || !!(hidden && hidden.required) || /\*\s*$/.test(lab || ''),
              filled: [...box.querySelectorAll(':scope > button')].some(b => b.getAttribute('aria-pressed') === 'true' || /selected|active|checked/i.test(b.className))});
  }
  const els = document.querySelectorAll('input, textarea, select');
  for (const el of els) {
    const type = (el.tagName === 'SELECT') ? 'select' : (el.tagName === 'TEXTAREA' ? 'textarea' : (el.type || 'text'));
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search', 'password'].includes(type)) continue;
    if (!vis(el) || el.disabled || el.hasAttribute('data-jp-yn')) continue;
    // react-select's invisible 'required' shadow input (Greenhouse, Ashby…) is not a real field
    if (type !== 'file' && el.getAttribute('aria-hidden') === 'true' && el.tabIndex < 0) continue;
    if (type === 'radio') {
      const name = el.name; if (!name || seenRadio.has(name)) continue; seenRadio.add(name);
      const group = [...document.querySelectorAll(`input[type=radio][name="${CSS.escape(name)}"]`)];
      const fs = el.closest('fieldset, [role=radiogroup], [role=group]');
      let q = fs ? ((fs.querySelector('legend') || {}).innerText || fs.getAttribute('aria-label') || '') : '';
      if (!q) { let n = fs || el.parentElement; for (let i = 0; n && i < 5 && !q; i++, n = n.parentElement) {
        const l = n.querySelector('legend, [class*="label" i], [class*="question" i], h3, h4, span');
        if (l && !group.some(g => l.contains(g)) && l.innerText.trim().length > 3) q = l.innerText; } }
      const id = 'jp' + (k++); group.forEach(g => g.setAttribute('data-jp', id));
      out.push({id, type, label: clean(q), name,
                options: group.map(g => clean(labelFor(g)) || g.value),
                values: group.map(g => g.value), required: group.some(g => g.required || g.getAttribute('aria-required') === 'true'),
                filled: group.some(g => g.checked)});
      continue;
    }
    if (type === 'checkbox') {
      const fs = el.closest('fieldset, [role=group]');
      let group = el.name ? [...document.querySelectorAll(`input[type=checkbox][name="${CSS.escape(el.name)}"]`)] : [];
      if (group.length < 2 && fs) group = [...fs.querySelectorAll('input[type=checkbox]')];
      if (group.length > 1) {
        const key = el.name || ('fs' + k);
        if (seenRadio.has('cb:' + key) || group.some(g => g.hasAttribute('data-jp-grouped'))) continue;
        seenRadio.add('cb:' + key);
        let q = fs ? ((fs.querySelector('legend') || {}).innerText || fs.getAttribute('aria-label') || '') : '';
        if (!q) { let n = (fs || el).parentElement; for (let i = 0; n && i < 5 && !q; i++, n = n.parentElement) {
          const l = n.querySelector('legend, label[id], [class*="label" i], [class*="question" i], h3, h4');
          if (l && !group.some(g => l.contains(g)) && l.innerText.trim().length > 3) q = l.innerText; } }
        const id = 'jp' + (k++); group.forEach(g => { g.setAttribute('data-jp', id); g.setAttribute('data-jp-grouped', '1'); });
        out.push({id, type: 'checkgroup', label: clean(q), name: el.name || '',
                  options: group.map(g => clean(labelFor(g)) || g.value),
                  required: !!(fs && /\*/.test((fs.innerText || '').slice(0, 300))) || group.some(g => g.required || g.getAttribute('aria-required') === 'true'),
                  filled: group.some(g => g.checked)});
        continue;
      }
    }
    const id = 'jp' + (k++); el.setAttribute('data-jp', id);
    const isCombo = el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list';
    let comboFilled = false;
    if (isCombo && !(el.value || '').trim()) {       // React dropdown: the choice shows in a sibling, not the input
      const box = el.closest('.select-shell') || (el.closest('[class*="__control" i]') || {}).parentElement || null;
      if (box) {
        const ph = box.querySelector('[id$="-placeholder"], [class*="placeholder" i]');
        const val = box.querySelector('[class*="singleValue" i], [class*="single-value" i], [class*="multiValue" i], [class*="multi-value" i]');
        comboFilled = !!val || (!ph && !!(box.innerText || '').trim());
      }
    }
    const f = {id, type, label: clean(labelFor(el)), name: el.name || '', elid: el.id || '', required: el.required || el.getAttribute('aria-required') === 'true',
               filled: type === 'checkbox' ? el.checked : (type === 'file' ? el.files.length > 0 : (!!(el.value || '').trim() || comboFilled)),
               accept: el.accept || '', ph: el.placeholder || '',
               ctx: type === 'file' ? (() => { let n = el.parentElement; for (let i = 0; n && i < 3; i++, n = n.parentElement) {
                   const t = (n.innerText || '').trim(); if (t) return t.slice(0, 300); } return ''; })() : '',
               combo: isCombo};
    if (type === 'select') { f.options = [...el.options].filter(o => o.value !== '' && !/^select|^choose|^--/i.test(o.text.trim())).map(o => clean(o.text));
      f.filled = el.selectedIndex > 0 && !/^select|^choose|^--/i.test((el.options[el.selectedIndex] || {}).text || ''); }
    out.push(f);
  }
  return out;
}
"""

BUTTON_JS = r"""
(patterns) => {
  document.querySelectorAll('[data-jp-btn]').forEach(e => e.removeAttribute('data-jp-btn'));
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const btns = [...document.querySelectorAll('button, input[type=submit], a[role=button], [role=button]')].filter(vis);
  for (const p of patterns) {
    const rx = new RegExp(p, 'i');
    const b = btns.find(b => rx.test((b.innerText || b.value || b.getAttribute('aria-label') || '').trim()) && !b.disabled);
    if (b) { b.setAttribute('data-jp-btn', p); return p; }
  }
  return null;
}
"""

SUBMIT_PATTERNS = [r"^submit (your )?application$", r"^submit$", r"^send application$",
                   r"^apply$", r"^submit and apply$", r"^finish$"]
NEXT_PATTERNS = [r"^continue$", r"^next$", r"^next step$", r"^save (and|&) continue$",
                 r"^review( your application)?$", r"^continue to review$", r"^proceed$"]
CONFIRM_RE = re.compile(r"application (has been |was )?(submitted|sent|received)|thank(s| you) for (applying|your application)|"
                        r"we('ve| have) received your application|you('ve| have) applied|application complete", re.I)
LOGIN_RE = re.compile(r"\b(sign in|log in|create (an )?account|password)\b", re.I)

RESUME_RE = re.compile(r"resume|résumé|\bcv\b|curriculum", re.I)
AUTOFILL_RE = re.compile(r"autofill|auto-fill|autocomplete from|upload your resume here to", re.I)
DATE_HINT_RE = re.compile(r"\bdate\b|mm/dd|dd/mm|yyyy", re.I)
COVER_RE = re.compile(r"cover letter|motivation letter|letter of interest", re.I)
ACK_RE = re.compile(r"\b(i (agree|accept|acknowledge|certify|consent|confirm|understand)|terms|privacy policy)\b", re.I)


@dataclass
class FieldResult:
    label: str
    type: str
    answer: str
    source: str
    required: bool
    options: list[str] = field(default_factory=list)


@dataclass
class StepReport:
    filled: list[FieldResult] = field(default_factory=list)
    missing_required: list[FieldResult] = field(default_factory=list)
    untrusted: list[FieldResult] = field(default_factory=list)   # AI-drafted answers
    labels: list[str] = field(default_factory=list)              # every field seen this step


def _as_date(value: str, iso: bool = False) -> str:
    """'2 weeks' / 'immediately' / '30 days' → a calendar date (fields that want a date)."""
    from datetime import date, timedelta
    v = (value or "").strip().lower()
    m = re.match(r"(\d+)\s*(day|week|month)", v)
    if m:
        n = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
    elif re.match(r"(immediate|asap|now)", v):
        n = 7
    else:
        return value
    d = date.today() + timedelta(days=n)
    return d.isoformat() if iso else d.strftime("%m/%d/%Y")


def _numeric(value: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k)?", value.replace(",", ""), re.I)
    if not m:
        return value
    v = float(m.group(1)) * (1000 if m.group(2) else 1)
    return str(int(v)) if v.is_integer() else str(v)


def frames(page):
    """Main frame first, then same-page iframes (e.g. Greenhouse embeds)."""
    out = [page.main_frame]
    for fr in page.frames:
        if fr is not page.main_frame and fr.url and fr.url.startswith("http"):
            out.append(fr)
    return out


class FormFiller:
    def __init__(self, bank: AnswerBank, resume: Path | None, job_title: str = "",
                 company: str = "", cover_letter: Callable[[], Path | None] | None = None,
                 cover_text: Callable[[], str] | None = None, allow_ai: bool = True,
                 cover_policy: str = "required"):
        self.bank = bank
        self.resume = resume
        self.job_title = job_title
        self.company = company
        self.cover_letter = cover_letter
        self.cover_text = cover_text
        self.cover_policy = cover_policy      # required | auto | always | never
        self.allow_ai = allow_ai
        self.all_fields: list[FieldResult] = []

    def fill_step(self, page) -> StepReport:
        report = StepReport()
        for fr in frames(page):
            try:
                fields = fr.evaluate(COLLECT_JS) or []
            except Exception:
                continue
            files = [f for f in fields if f["type"] == "file" and not f.get("filled")]
            if files:
                uploaded = False
                for f in files:
                    res = self._fill_field(fr, f)
                    if res is not None and res.source == "file":
                        uploaded = True
                        report.filled.append(res)
                        self.all_fields.append(res)
                if uploaded:
                    try:
                        fr.wait_for_timeout(2500)          # let the page parse/re-render
                        fields = fr.evaluate(COLLECT_JS) or []
                    except Exception:
                        pass
                fields = [f for f in fields if f["type"] != "file" or f.get("filled")]
            for f in fields:
                res = self._fill_field(fr, f)
                if res is None:
                    continue
                report.labels.append(f"{res.type}:{res.label}")
                if res.source != "existing" or not any(x.label == res.label for x in self.all_fields):
                    self.all_fields.append(res)
                if res.answer:
                    report.filled.append(res)
                    if res.source not in ("profile", "bank", "eeo", "file", "existing", "cover"):
                        report.untrusted.append(res)
                elif res.required:
                    report.missing_required.append(res)
        if report.filled or report.missing_required:
            report.missing_required = self._still_missing(page, report)
        return report

    def _still_missing(self, page, report: StepReport) -> list[FieldResult]:
        """Re-read the form after filling: what is *actually* still empty and required.
        (Dropdowns re-render, async pickers settle late, shadow inputs disappear.)"""
        try:
            page.wait_for_timeout(800)
        except Exception:
            pass
        known = {r.label: r for r in report.filled + report.missing_required}
        out: list[FieldResult] = []
        for fr in frames(page):
            try:
                fields = fr.evaluate(COLLECT_JS) or []
            except Exception:
                continue
            for f in fields:
                if not f.get("required") or f.get("filled"):
                    continue
                if f["type"] == "file" and not COVER_RE.search(f"{f.get('label', '')} {f.get('elid', '')}") \
                        and any(r.source == "file" for r in report.filled):
                    continue
                prev = known.get(f.get("label", ""))
                if prev and prev.answer and f.get("combo"):
                    continue          # option was clicked; some widgets don't reflect it in the input
                out.append(FieldResult(f.get("label", ""), f["type"], "",
                                       prev.source if prev and not prev.answer else "none", True,
                                       f.get("options") or (prev.options if prev else [])))
        return out

    def _fill_field(self, fr, f: dict) -> FieldResult | None:
        label, typ, sel = f.get("label", ""), f["type"], f'[data-jp="{f["id"]}"]'
        options = f.get("options") or []
        if f.get("filled"):
            return FieldResult(label, typ, "(prefilled)", "existing", f.get("required", False), options)

        hint = f"{label} {f.get('name', '')} {f.get('elid', '').replace('_', ' ')}"
        if COVER_RE.search(hint) and typ in ("file", "textarea"):
            if self.cover_policy == "never" or (self.cover_policy == "required" and not f.get("required")):
                return FieldResult(label or "cover letter", typ, "", "skipped", False)

        if typ == "file":
            if AUTOFILL_RE.search(f"{label} {f.get('ctx', '')}"):
                # "autofill from resume" boxes re-write the whole form; applyloop fills it itself
                return FieldResult(label or "autofill", typ, "", "skipped", False)
            target = None
            if COVER_RE.search(hint):
                target = self.cover_letter() if self.cover_letter else None
            elif RESUME_RE.search(hint) or not COVER_RE.search(hint):
                target = self.resume
            if target and Path(target).exists():
                try:
                    fr.set_input_files(sel, str(target))
                    return FieldResult(label or "resume", typ, Path(target).name, "file", f.get("required", False))
                except Exception:
                    pass
            return FieldResult(label, typ, "", "none", f.get("required", False))

        if typ == "checkbox":
            # ticked only when one of YOUR answers.yml rules says yes (e.g. the acknowledgement
            # rule); never on an AI guess. Otherwise left for you.
            ans = self.bank.resolve(label, typ, [], self.job_title, self.company, allow_ai=False)
            if ans.value and ans.source == "bank" and \
                    not re.match(r"\s*(no|false|decline)", ans.value, re.I):
                try:
                    fr.locator(sel).check(force=True)
                    return FieldResult(label, typ, "checked", ans.source, f.get("required", False))
                except Exception:
                    pass
            return FieldResult(label, typ, "", "ack" if ACK_RE.search(label) else "none", f.get("required", False))

        if typ == "textarea" and COVER_RE.search(label) and self.cover_text:
            text = self.cover_text()
            if text:
                fr.fill(sel, text)
                return FieldResult(label, typ, text[:60] + "…", "cover", f.get("required", False))

        if f.get("combo") and not options:
            options = _combo_options(fr, sel)               # open the dropdown to read its choices
        ans: Answer = self.bank.resolve(label, typ, options, self.job_title, self.company,
                                        allow_ai=self.allow_ai)
        if f.get("combo") and len(options) >= 50 and (not ans.value or ans.source == "ai"):
            # long searchable dropdown (schools, cities): only the first options were readable —
            # use your saved answer and type it into the search box
            own = self.bank.resolve(label, typ, [], self.job_title, self.company, allow_ai=False)
            if own.value and own.source in ("bank", "profile", "eeo"):
                ans = own
        if not ans.value:
            return FieldResult(label, typ, "", ans.source, f.get("required", False), options)
        value = _numeric(ans.value) if typ == "number" else ans.value
        if typ == "date" or DATE_HINT_RE.search(f.get("ph", "")):
            value = _as_date(value, iso=(typ == "date"))
        try:
            if typ == "yesno":
                pick = "Yes" if re.match(r"\s*yes", value, re.I) else "No" if re.match(r"\s*no", value, re.I) else ""
                if not pick:
                    return FieldResult(label, typ, "", "none", f.get("required", False), options)
                loc = fr.locator(sel)
                loc.nth(0 if (loc.nth(0).inner_text().strip().lower() == pick.lower()) else 1).click()
                value = pick
            elif typ == "select":
                fr.select_option(sel, label=value)
            elif typ == "radio":
                idx = options.index(value) if value in options else -1
                if idx < 0:
                    return FieldResult(label, typ, "", "none", f.get("required", False), options)
                fr.locator(sel).nth(idx).check(force=True)
            elif typ == "checkgroup":
                wanted = [v.strip() for v in value.split(";") if v.strip()] or [value]
                hit = False
                for w in wanted:
                    if w in options:
                        fr.locator(sel).nth(options.index(w)).check(force=True)
                        hit = True
                if not hit:
                    return FieldResult(label, typ, "", "none", f.get("required", False), options)
            elif f.get("combo"):                   # React-style dropdown (Greenhouse, Ashby...)
                if not _combo_pick(fr, sel, value):
                    return FieldResult(label, typ, "", "none", f.get("required", False), options)
            else:
                fr.fill(sel, value)
                if typ != "date" and DATE_HINT_RE.search(f.get("ph", "")):
                    fr.press(sel, "Escape")            # close the calendar pop-up
        except Exception:
            return FieldResult(label, typ, "", "none", f.get("required", False), options)
        return FieldResult(label, typ, value, ans.source, f.get("required", False), options)


OPTIONS_JS = r"""
() => [...document.querySelectorAll('[role=option], [class*="option" i][id*="option" i]')]
        .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean)
"""

PICK_JS = r"""
(v) => {
  const n = s => (s || '').toLowerCase().replace(/[^a-z0-9+]+/g, ' ').trim();
  const os = [...document.querySelectorAll('[role=option], [class*="option" i][id*="option" i]')]
               .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  const t = n(v);
  const o = os.find(e => n(e.innerText) === t) ||
            (t.length > 3 ? (os.find(e => n(e.innerText).startsWith(t)) || os.find(e => n(e.innerText).includes(t))) : null);
  if (o) { o.click(); return (o.innerText || '').trim(); }
  return null;
}
"""


def _combo_options(fr, sel) -> list[str]:
    try:
        fr.click(sel)
        fr.wait_for_timeout(500)
        opts = fr.evaluate(OPTIONS_JS) or []
        fr.press(sel, "Escape")
        return list(dict.fromkeys(opts))[:300]
    except Exception:
        return []


def _combo_pick(fr, sel, value: str) -> bool:
    try:
        fr.click(sel)
        fr.wait_for_timeout(300)
        if fr.evaluate(PICK_JS, value):            # visible without typing
            return True
        fr.fill(sel, value)                        # type to filter long lists (countries…)
        for _ in range(8):                         # async pickers (schools, cities) load results late
            fr.wait_for_timeout(500)
            if fr.evaluate(PICK_JS, value):
                return True
        fr.press(sel, "Escape")
    except Exception:
        pass
    return False


def find_button(page, patterns: list[str]):
    """Return (frame, pattern) for the first visible button matching patterns."""
    for fr in frames(page):
        try:
            hit = fr.evaluate(BUTTON_JS, patterns)
        except Exception:
            continue
        if hit:
            return fr, hit
    return None, None


def click_button(fr, pattern: str) -> None:
    fr.click(f'[data-jp-btn="{pattern}"]')


def page_confirms_submission(page) -> bool:
    for fr in frames(page):
        try:
            txt = fr.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            continue
        if CONFIRM_RE.search(txt):
            return True
    return bool(re.search(r"post-apply|confirmation|thank-?you|applied", page.url, re.I))


def needs_login(page) -> bool:
    try:
        has_pw = page.query_selector("input[type=password]") is not None
    except Exception:
        has_pw = False
    return has_pw
