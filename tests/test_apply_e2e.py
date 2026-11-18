"""End-to-end apply-engine tests against a local multi-step Easy-Apply style form."""

import re
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

from applyloop import store as S  # noqa: E402
from applyloop.answers import AnswerBank  # noqa: E402
from applyloop.apply_engine.engine import apply_job  # noqa: E402
from applyloop.models import CandidateProfile  # noqa: E402
from applyloop.sources import indeed  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


class _Settings:
    def __init__(self, root):
        self.root = root
        self.apply = {"screenshots": True}


@pytest.fixture(scope="module")
def ctx():
    try:
        p = pw.sync_playwright().start()
        browser = p.chromium.launch(headless=True)
    except Exception as e:  # browser not installed
        pytest.skip(f"chromium unavailable: {e}")
    context = browser.new_context()
    yield context
    context.close()
    browser.close()
    p.stop()


def _bank():
    prof = CandidateProfile(full_name="Jane Smith", email="u@example.com", phone="555-0100",
                            city="Atlanta", country="United States")
    return AnswerBank(prof, rules=[
        {"match": ["authori[sz]ed to work"], "answer": "Yes"},
        {"match": ["require (visa )?sponsorship"], "answer": "No"},
        {"match": ["years of (professional |relevant |work )?experience"], "answer": "{years_experience}"},
    ], defaults={"years_experience": 7})


def _job(st, url):
    jid, _ = st.upsert_job(url, title="Senior Data Engineer", company="Acme", source="company")
    st.update_job(jid, apply_url=url)
    return st.get(jid)


def test_queue_mode_auto_submits_when_all_answers_trusted(ctx, tmp_path):
    st = S.Store(":memory:")
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    job = _job(st, (FIX / "easy_apply.html").as_uri() + "#nowhy")
    out = apply_job(ctx, st, job, _Settings(tmp_path), _bank(), mode="queue", interactive=False,
                    resume=resume, approved=True)
    assert out.status == S.APPLIED, out.detail
    assert any("confirmation" in s for s in out.screenshots)
    qs = {q["question"]: (q["answer"], q["source"]) for q in st.questions_for(job["id"])}
    assert qs["What is your ethnicity?"] == ("I don't wish to answer", "eeo")
    assert qs["How many years of experience do you have with Snowflake?"][0] == "6-10"


def test_unknown_required_question_parks_job(ctx, tmp_path):
    st = S.Store(":memory:")
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    job = _job(st, (FIX / "easy_apply.html").as_uri())
    out = apply_job(ctx, st, job, _Settings(tmp_path), _bank(), mode="queue", interactive=False,
                    resume=resume, approved=True)
    assert out.status == S.NEEDS_INPUT
    assert "Why do you want to work at Acme" in out.detail


def test_not_approved_never_submits_unattended(ctx, tmp_path):
    st = S.Store(":memory:")
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    job = _job(st, (FIX / "easy_apply.html").as_uri() + "#nowhy")
    out = apply_job(ctx, st, job, _Settings(tmp_path), _bank(), mode="review", interactive=False,
                    resume=resume, approved=False)
    assert out.status == S.NEEDS_INPUT and out.detail == "review mode"


def test_indeed_card_parser(ctx):
    page = ctx.new_page()
    page.set_content((FIX / "indeed_search.html").read_text())
    rows = page.evaluate(indeed.CARDS_JS)
    page.close()
    by = {r["jk"]: r for r in rows}
    assert set(by) == {"abc123", "def456"}
    assert by["abc123"]["company"] == "Acme Corp" and by["abc123"]["easy"] is True
    assert by["abc123"]["salary"].startswith("$150,000")
    assert by["def456"]["easy"] is False


def test_filler_values_land_in_form(ctx, tmp_path):
    from applyloop.apply_engine.forms import FormFiller, click_button, find_button
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    page = ctx.new_page()
    page.goto((FIX / "easy_apply.html").as_uri() + "#nowhy")
    filler = FormFiller(_bank(), resume, "Senior Data Engineer", "Acme")
    for _ in range(3):
        filler.fill_step(page)
        fr, pat = find_button(page, [r"^continue$"])
        click_button(fr, pat)
    fr, pat = find_button(page, [r"^submit your application$"])
    click_button(fr, pat)
    got = page.evaluate("() => window.__answers")
    page.close()
    assert got == {"ln": "Smith", "em": "u@example.com", "cv": 1, "auth": "y", "spons": "n",
                   "yrs": "6-10", "eth": "I don't wish to answer"}


def test_combobox_and_checkbox_group(ctx, tmp_path):
    from applyloop.apply_engine.forms import FormFiller
    prof = CandidateProfile(full_name="Jane Smith", email="u@example.com", country="United States")
    bank = AnswerBank(prof, rules=[
        {"match": ["require (visa )?sponsorship", "sponsor (you|me)", "work permit"], "answer": "No"},
        {"match": ["(ever )?(been )?(employed|worked) (by|for|at)"], "answer": "{previously_employed}"},
        {"match": ["countr(y|ies) you (anticipate|expect|plan)"], "answer": "United States"},
    ], defaults={"past_employers": ["Acme Corp", "Globex"]})
    page = ctx.new_page()
    page.goto((FIX / "combo_form.html").as_uri())
    f = FormFiller(bank, None, "Data Scientist", "Stripe")
    rep = f.fill_step(page)
    picked = page.evaluate("() => window.picked")
    checked = page.evaluate("() => [...document.querySelectorAll('input[type=checkbox]:checked')].map(e => e.value)")
    page.close()
    assert picked == {"c1": "United States of America", "c2": "No", "c3": "No"}, (picked, rep)
    assert checked == ["US"]
    assert not rep.missing_required


def test_ashby_style_form(ctx, tmp_path):
    from applyloop.apply_engine.forms import FormFiller
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    prof = CandidateProfile(full_name="Jane Smith", email="u@example.com", phone="555-010-0000")
    bank = AnswerBank(prof, rules=[
        {"match": ["authori[sz]ed to work"], "answer": "Yes"},
        {"match": ["require (visa )?sponsorship", "sponsorship"], "answer": "No"},
        {"match": ["when can you start", "start date"], "answer": "{notice_period}"},
    ], defaults={"notice_period": "2 weeks"})
    page = ctx.new_page()
    page.goto((FIX / "ashby_form.html").as_uri())
    rep = FormFiller(bank, resume, "Data Engineer", "OpenAI", allow_ai=False).fill_step(page)
    state = page.evaluate("""() => ({auto: !!window.autofilled, nm: nm.value, em: em.value, ph: ph.value, dt: dt.value,
        rs: rs.files.length, y1: [...document.querySelectorAll('.yesno')][0].querySelector('[aria-pressed=true]')?.innerText,
        y2: [...document.querySelectorAll('.yesno')][1].querySelector('[aria-pressed=true]')?.innerText})""")
    page.close()
    assert not state["auto"]                                   # autofill uploader left alone
    assert state["nm"] == "Jane Smith" and state["em"] == "u@example.com" and state["ph"] == "555-010-0000"
    assert state["rs"] == 1 and state["y1"] == "Yes" and state["y2"] == "No"
    assert re.match(r"\d\d/\d\d/\d{4}$", state["dt"])
    assert not rep.missing_required, rep.missing_required
