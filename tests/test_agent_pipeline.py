"""Agent triage → score → CV preparation, no browser and no AI (keyword scoring)."""

import shutil
from pathlib import Path

import pytest

from applyloop import store as S
from applyloop.agent import Agent, HuntSummary
from applyloop.config import ProjectPaths
from applyloop.latex_cv import tex_engine
from applyloop.llm import LLM, LLMConfig
from applyloop.settings import DEFAULTS, Settings, Track

TEX = (Path(__file__).parent / "test_agent_core.py").read_text().split('TEX = r"""')[1].split('"""')[0]


@pytest.fixture
def agent(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "profile.yml").write_text(
        "candidate:\n  full_name: Test Person\n  email: t@example.com\n")
    cvdir = tmp_path / "cvs"
    cvdir.mkdir()
    (cvdir / "de.tex").write_text(TEX)
    (cvdir / "ml.tex").write_text(TEX.replace("Data Engineer, Acme", "ML Engineer, Acme")
                                  .replace("Airflow, dbt.", "Airflow, dbt, PyTorch, Kafka."))
    tracks = [Track("data_engineering", cv=cvdir / "de.tex", max_pages=1, min_score=2.0,
                    titles=["data engineer"], keywords=["spark", "airflow", "dbt", "kafka"]),
              Track("ml_engineering", cv=cvdir / "ml.tex", max_pages=1, min_score=2.0,
                    titles=["ml engineer"], keywords=["pytorch", "llm"])]
    sections = {k: dict(v) for k, v in DEFAULTS.items()}
    sections["filters"]["title_exclude"] = ["intern"]
    settings = Settings(root=tmp_path, tracks=tracks, cv_dir=cvdir, **sections)
    return Agent(ProjectPaths(tmp_path), settings, interactive=False, store=S.Store(":memory:"),
                 llm=LLM(LLMConfig()))


def test_triage_score_prepare(agent):
    st = agent.store
    good, _ = st.upsert_job("https://www.indeed.com/viewjob?jk=1", title="Senior Data Engineer",
                            company="Acme", source="indeed",
                            description="Spark, Airflow, dbt and Kafka pipelines on AWS.")
    intern, _ = st.upsert_job("https://www.indeed.com/viewjob?jk=2", title="Data Engineer Intern",
                              company="Acme", source="indeed", description="Spark")
    summary = HuntSummary()
    assert agent.triage(None, good, summary) is True
    assert agent.triage(None, intern, summary) is False
    assert st.get(intern)["status"] == S.FILTERED

    score = agent.evaluate(good)
    row = st.get(good)
    assert row["track"] == "data_engineering" and score >= 2.0 and row["grade"] == "kw"

    if not tex_engine():
        pytest.skip("no TeX engine")
    assert agent.prepare(good)
    row = st.get(good)
    assert row["cv_pdf"].endswith(".pdf") and Path(row["cv_pdf"]).exists()
    assert row["cv_tailored"] == 1                    # Kafka exists in the ML CV → surfaced
    assert "Kafka" in Path(row["cv_tex"]).read_text()
    assert "kafka" in row["tailor_notes"]


def test_daily_cap(agent):
    agent.settings.apply["daily_cap"] = 1
    jid, _ = agent.store.upsert_job("https://x.com/1", title="Data Engineer", company="A")
    agent.store.set_status(jid, S.APPLIED)
    other, _ = agent.store.upsert_job("https://x.com/2", title="Data Engineer", company="B")
    assert agent.apply_one(None, other, "queue") == "cap"


def test_add_job_manual_with_jsonld(agent, tmp_path, monkeypatch):
    import applyloop.agent as A
    page = """<html><head><title>x</title><script type="application/ld+json">
    {"@type":"JobPosting","title":"Senior Data Engineer","hiringOrganization":{"name":"Acme"},
     "jobLocationType":"TELECOMMUTE","jobLocation":{"address":{"addressLocality":"Atlanta","addressRegion":"GA"}},
     "description":"<p>Build Spark, Airflow, dbt and Kafka pipelines on AWS. """ + "More detail. " * 30 + """</p>"}
    </script></head><body>hi</body></html>"""

    class R:
        def read(self):
            return page.encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: R())
    jid = agent.add_job("https://boards.greenhouse.io/acme/jobs/1")
    row = agent.store.get(jid)
    assert row["company"] == "Acme" and row["title"] == "Senior Data Engineer"
    assert "Remote" in row["location"] and "Kafka" in row["description"]
    assert row["track"] == "data_engineering" and row["status"] in ("queued", "failed", "scored")
    # same URL again → not duplicated
    assert agent.add_job("https://boards.greenhouse.io/acme/jobs/1?gh_src=x") == jid


def test_automation_verdict():
    from applyloop.apply_engine.engine import automation_verdict
    assert automation_verdict("https://boards.greenhouse.io/acme/jobs/1")[0] is True
    assert automation_verdict("https://jobs.lever.co/acme/123/apply")[0] is True
    assert automation_verdict("https://www.indeed.com/viewjob?jk=1")[0] is False
    assert automation_verdict("https://acme.wd5.myworkdayjobs.com/x")[0] is False
    assert automation_verdict("https://careers.acme.com/jobs/9")[0] is True


def test_autopilot_routes_jobs(agent, monkeypatch):
    st = agent.store
    a, _ = st.upsert_job("https://boards.greenhouse.io/a/jobs/1", title="Data Engineer", company="A")
    b, _ = st.upsert_job("https://www.indeed.com/viewjob?jk=2", title="Data Engineer", company="B")
    c, _ = st.upsert_job("https://jobs.lever.co/c/3", title="Data Engineer", company="C")
    for jid, score in ((a, 4.5), (b, 4.6), (c, 3.6)):
        st.update_job(jid, score=score)
        st.set_status(jid, S.QUEUED, "ready")
    seen = []

    def fake_apply(ctx, jid, mode, manual=False):
        seen.append((jid, st.get(jid)["status"], mode))
        st.set_status(jid, S.APPLIED, "confirmed")
        return S.APPLIED

    monkeypatch.setattr(agent, "apply_one", fake_apply)
    monkeypatch.setattr("applyloop.apply_engine.engine.human_delay", lambda s: None)
    summary = HuntSummary()
    agent._autopilot(None, {"min_score": 4.0}, None, summary)
    assert seen == [(a, S.APPROVED, "queue")]              # greenhouse ≥ 4.0 → auto-applied
    assert st.get(b)["status"] == S.MANUAL                  # indeed → handed to you
    assert st.get(c)["status"] == S.QUEUED                  # below threshold → waits
    assert summary.applied == 1 and summary.manual == 1
    d = agent.write_digest(summary)
    text = d.read_text()
    assert "Applied" in text and "Apply yourself" in text


def test_direct_form_url():
    from applyloop.apply_engine.engine import direct_form_url

    class Sx:
        search = {"companies": [{"name": "Stripe", "ats": "greenhouse", "slug": "stripe"}]}
    j = {"apply_url": "https://stripe.com/jobs/search?gh_jid=7123456", "url": "", "source": "greenhouse",
         "company": "Stripe"}
    assert direct_form_url(j, Sx) == "https://boards.greenhouse.io/embed/job_app?for=stripe&token=7123456"
    j = {"apply_url": "https://jobs.lever.co/p/0f0e5b4e-1111-2222-3333-444455556666", "url": "",
         "source": "lever", "company": "P"}
    assert direct_form_url(j).endswith("/apply")
    assert direct_form_url({"apply_url": "https://careers.x.com/1", "url": "", "source": "adzuna",
                            "company": "X"}) == ""


def test_glassdoor_job_rerouted_to_company_site(agent, monkeypatch):
    from applyloop.sources import JobPosting, ats_api
    assert ats_api.slug_candidates("Scale AI, Inc.") == ["scaleai", "scale-ai"]
    st = agent.store
    jid, _ = st.upsert_job("https://www.glassdoor.com/job-listing/x?jl=1", title="Senior Data Engineer",
                           company="Acme Robotics", source="glassdoor", description="Spark")
    monkeypatch.setattr(ats_api, "find_board", lambda c: [
        JobPosting(url="https://jobs.lever.co/acmerobotics/0f0e5b4e-1111-2222-3333-444455556666",
                   title="Data Engineer, Senior", company=c, source="lever",
                   apply_url="https://jobs.lever.co/acmerobotics/0f0e5b4e-1111-2222-3333-444455556666/apply",
                   description="Spark, Airflow, dbt " * 20)])
    row = agent._reroute(jid)
    assert "lever.co" in row["apply_url"] and not agent._behind_bot_wall(row)


def test_glassdoor_only_job_parked_not_automated(agent, monkeypatch):
    from applyloop.sources import ats_api
    monkeypatch.setattr(ats_api, "find_board", lambda c: [])
    st = agent.store
    jid, _ = st.upsert_job("https://www.glassdoor.com/job-listing/y?jl=2", title="Data Engineer",
                           company="Nowhere Co", source="glassdoor")
    st.update_job(jid, cv_pdf=__file__, track="data_engineering")
    called = []
    monkeypatch.setattr("applyloop.apply_engine.engine.apply_job", lambda *a, **k: called.append(1))
    assert agent.apply_one(object(), jid, "queue") == S.MANUAL     # no browser attempt
    assert not called and "bot checks" in st.get(jid)["status_detail"]


def test_rating_stripped_and_workday(monkeypatch):
    from applyloop.sources import JobPosting, ats_api
    p = JobPosting(url="u", title="4.8 — Senior Data Engineer", company="D&G (VA) 4.8 ★")
    assert (p.company, p.title) == ("D&G (VA)", "Senior Data Engineer")
    assert ats_api.workday_parts("https://homedepot.wd5.myworkdayjobs.com/en-US/CareerDepot") == \
        ("https://homedepot.wd5.myworkdayjobs.com", "homedepot", "CareerDepot")
    calls = []

    def fake_post(url, payload, timeout=30):
        calls.append(payload["searchText"])
        if payload["offset"]:
            return {"jobPostings": []}
        return {"jobPostings": [
            {"title": "Senior Data Engineer", "externalPath": "/job/Atlanta/Senior-Data-Engineer_R1",
             "postedOn": "Posted 3 Days Ago", "locationsText": "Atlanta, GA"},
            {"title": "Store Associate", "externalPath": "/job/Atlanta/Associate_R2", "postedOn": "Posted Today"},
            {"title": "Data Engineer", "externalPath": "/job/x/Old_R3", "postedOn": "Posted 30+ Days Ago"}]}
    monkeypatch.setattr(ats_api, "_post", fake_post)
    monkeypatch.setattr(ats_api, "_get", lambda url, timeout=30: {"jobPostingInfo": {
        "jobDescription": "<p>Spark and Airflow</p>", "location": "Atlanta, GA", "startDate": "2026-09-30"}})
    jobs = ats_api.fetch_company({"name": "The Home Depot", "ats": "workday",
                                  "url": "https://homedepot.wd5.myworkdayjobs.com/CareerDepot"},
                                 7, ["data engineer", "analytics engineer"], lambda t: "engineer" in t.lower())
    assert calls == ["data engineer", "analytics engineer"]
    assert len(jobs) == 1 and jobs[0].description == "Spark and Airflow"
    assert jobs[0].apply_url == "https://homedepot.wd5.myworkdayjobs.com/CareerDepot/job/Atlanta/Senior-Data-Engineer_R1/apply"


def test_resolve_adzuna_links(agent, monkeypatch):
    import applyloop.agent as A
    from applyloop.cli_agent import apply_route
    from applyloop.sources import ats_api
    monkeypatch.setattr(ats_api, "find_board", lambda c: [])
    st = agent.store
    lands = {"https://www.adzuna.com/land/ad/1": "https://jobs.lever.co/acme/0f0e5b4e-1111-2222-3333-444455556666",
             "https://www.adzuna.com/land/ad/2": "https://acme.wd5.myworkdayjobs.com/Careers/job/x",
             "https://www.adzuna.com/details/3": "https://www.adzuna.com/details/3"}
    ids = []
    for i, u in enumerate(lands, 1):
        jid, _ = st.upsert_job(f"https://x.com/{i}", title="Data Engineer", company=f"C{i}", source="adzuna",
                               apply_url=u)
        st.set_status(jid, S.QUEUED, "ready")
        ids.append(jid)
    assert apply_route(st.get(ids[0]))[1] == "?"
    monkeypatch.setattr(A, "_land", lambda page, url, wait_s=12: lands[url])

    class Ctx:
        def new_page(self):
            class P:
                def close(self): pass
            return P()
    counts = agent.resolve_links(Ctx())
    assert counts == {"auto": 1, "you": 1, "unresolved": 1}
    assert apply_route(st.get(ids[0])) == ("jobs.lever.co", "auto")
    assert apply_route(st.get(ids[1]))[1] == "you"
    assert agent.resolve_links(Ctx(), skip_failed=True) == {"auto": 0, "you": 0, "unresolved": 0}


def test_autopilot_skips_reposted_duplicates(agent, monkeypatch):
    st = agent.store
    a, _ = st.upsert_job("https://boards.greenhouse.io/a/jobs/1", title="Lead Applied AI Engineer - Remote",
                         company="Appian Corporation", location="Remote")
    b, _ = st.upsert_job("https://boards.greenhouse.io/a/jobs/2", title="Lead Applied AI Engineer - Remote",
                         company="Appian Corporation", location="McLean, VA")
    for j in (a, b):
        st.update_job(j, score=4.5)
        st.set_status(j, S.QUEUED, "ready")
    seen = []
    monkeypatch.setattr(agent, "apply_one", lambda ctx, jid, mode, manual=False: seen.append(jid) or S.NEEDS_INPUT)
    agent._autopilot(None, {"min_score": 4.0}, None, HuntSummary())
    assert seen == [a] and st.get(b)["status"] == S.SKIPPED and "duplicate" in st.get(b)["status_detail"]


def test_track_rotation(agent, monkeypatch):
    from datetime import date
    agent.settings.apply["auto"] = {"track_rotation": ["data_engineering", "ml_engineering"]}
    d = date(2026, 10, 5)
    assert {agent.track_of_the_day(d), agent.track_of_the_day(date(2026, 10, 6))} == {"data_engineering", "ml_engineering"}
    agent.settings.apply["auto"] = {"track_rotation": {"mon": "ml_engineering", "default": "data_engineering"}}
    assert agent.track_of_the_day(d) == "ml_engineering"            # 2026-10-05 is a Monday
    assert agent.track_of_the_day(date(2026, 10, 6)) == "data_engineering"
    st = agent.store
    ids = {}
    for t, sc in (("data_engineering", 4.9), ("ml_engineering", 4.1)):
        jid, _ = st.upsert_job(f"https://boards.greenhouse.io/a/jobs/{t}", title=f"{t} role", company=t)
        st.update_job(jid, score=sc, track=t)
        st.set_status(jid, S.QUEUED, "ready")
        ids[t] = jid
    order = []
    monkeypatch.setattr(agent, "track_of_the_day", lambda day=None: "ml_engineering")
    monkeypatch.setattr(agent, "apply_one", lambda ctx, jid, mode, manual=False: order.append(jid) or S.NEEDS_INPUT)
    agent._autopilot(None, {"min_score": 4.0}, None, HuntSummary())
    assert order == [ids["ml_engineering"], ids["data_engineering"]]   # today's track first
    order.clear()
    for j in ids.values():
        st.set_status(j, S.QUEUED, "ready")
    agent._autopilot(None, {"min_score": 4.0, "rotation_only": True}, None, HuntSummary())
    assert order == [ids["ml_engineering"]]



def test_workday_pagination(monkeypatch):
    from applyloop.sources import ats_api
    pages = []

    def fake_post(url, payload, timeout=30):
        pages.append(payload["offset"])
        start = payload["offset"]
        n = max(0, min(20, 45 - start))
        return {"total": 45 if start == 0 else 0, "jobPostings": [
            {"title": f"Data Engineer {start + i}", "externalPath": f"/job/x/DE_{start + i}", "postedOn": "Posted Today"}
            for i in range(n)]}
    monkeypatch.setattr(ats_api, "_post", fake_post)
    monkeypatch.setattr(ats_api, "_get", lambda url, timeout=30: {"jobPostingInfo": {"jobDescription": "x"}})
    jobs = ats_api.workday("https://acme.wd1.myworkdayjobs.com/Careers", "Acme", ["data engineer"], max_details=100)
    assert pages == [0, 20, 40] and len(jobs) == 45


def test_review_mode_uses_same_picking(agent, monkeypatch):
    st = agent.store
    gh, _ = st.upsert_job("https://boards.greenhouse.io/a/jobs/9", title="Data Engineer", company="A")
    gd, _ = st.upsert_job("https://www.glassdoor.com/job-listing/x?jl=9", title="Data Engineer", company="B")
    for j in (gh, gd):
        st.update_job(j, score=4.5, track="data_engineering")
        st.set_status(j, S.QUEUED, "ready")
    from applyloop.sources import ats_api
    monkeypatch.setattr(ats_api, "find_board", lambda c: [])
    calls = []
    monkeypatch.setattr(agent, "apply_one", lambda ctx, jid, mode, manual=False: calls.append((jid, mode, st.get(jid)["status"])) or S.APPLIED)
    monkeypatch.setattr("applyloop.apply_engine.engine.human_delay", lambda s: None)
    agent._autopilot(None, {"min_score": 4.0}, None, HuntSummary(), mode="review")
    assert calls == [(gh, "review", S.QUEUED)]          # not pre-approved; you confirm at the end
    assert st.get(gd)["status"] == S.MANUAL               # job-board-only → listed for you, not opened


def test_same_form_never_applied_twice(agent, monkeypatch):
    st = agent.store
    form = "https://boards.greenhouse.io/embed/job_app?for=appian&token=8204996"
    a, _ = st.upsert_job("https://x.com/a1", title="Lead Applied AI Engineer - Remote", company="Appian Corporation",
                         location="Bee Cave", apply_url=form)
    st.set_status(a, S.APPLIED, "confirmed")
    b, _ = st.upsert_job("https://x.com/a2", title="Lead Applied AI Engineer (Remote)", company="Appian",
                         location="Campbell", apply_url=form)
    st.update_job(b, score=4.5, track="ml_engineering")
    st.set_status(b, S.QUEUED, "ready")
    monkeypatch.setattr(agent, "apply_one", lambda *a, **k: (_ for _ in ()).throw(AssertionError("applied twice")))
    agent._autopilot(None, {"min_score": 4.0}, None, HuntSummary())
    assert st.get(b)["status"] == S.SKIPPED


def test_manual_walk(agent, monkeypatch):
    import applyloop.apply_engine.engine as E
    st = agent.store
    ids = []
    for i, sc in enumerate((3.6, 4.4, 4.0)):
        jid, _ = st.upsert_job(f"https://x.com/m{i}", title="Data Engineer", company=f"M{i}")
        st.update_job(jid, score=sc, track="data_engineering", cv_pdf=__file__)
        st.set_status(jid, S.MANUAL, "apply yourself")
        ids.append(jid)
    answers = iter(["y", "q"])
    seen = []

    def fake_manual(job, resume, ans):
        seen.append(job["id"])
        return E.ApplyOutcome(S.APPLIED, "submitted by you") if next(answers) == "y" else E.ApplyOutcome("stop")
    monkeypatch.setattr(E, "manual_apply", fake_manual)
    agent.submit(manual=True)
    assert seen == [ids[1], ids[2]]                      # best score first, stopped on q
    assert st.get(ids[1])["status"] == S.APPLIED and st.get(ids[2])["status"] == S.MANUAL


def test_per_company_cap(agent, monkeypatch):
    st = agent.store
    ids = []
    for i in range(3):
        jid, _ = st.upsert_job(f"https://boards.greenhouse.io/air/jobs/{i}", title=f"ML Engineer {i}", company="Airbnb")
        st.update_job(jid, score=4.5, track="ml_engineering")
        st.set_status(jid, S.QUEUED, "ready")
        ids.append(jid)
    calls = []

    def fake(ctx, jid, mode, manual=False):
        calls.append(jid)
        st.set_status(jid, S.APPLIED, "ok")
        st.update_job(jid, applied_at=__import__("datetime").datetime.now().isoformat(timespec="seconds"))
        return S.APPLIED
    monkeypatch.setattr(agent, "apply_one", fake)
    monkeypatch.setattr("applyloop.apply_engine.engine.human_delay", lambda s: None)
    agent._autopilot(None, {"min_score": 4.0, "max_per_company_per_week": 2}, None, HuntSummary())
    assert len(calls) == 2 and st.get([j for j in ids if j not in calls][0])["status"] == S.QUEUED


def test_reposts_skipped_at_discovery(agent):
    from applyloop.sources import JobPosting
    st = agent.store
    a, _ = st.upsert_job("https://x.com/cog/1", title="Senior Agentic AI Engineer (Remote)", company="Cognizant",
                         location="Atlanta")
    st.set_status(a, S.QUEUED, "ready")
    agent.settings.filters["title_exclude"] = []
    agent.settings.search.update({"sources": [], "companies": [], "posted_within_days": 0})
    import applyloop.agent as A
    posts = [JobPosting(url="https://x.com/cog/2", title="Senior Agentic AI Engineer - Remote", company="Cognizant",
                        location="Dallas"),
             JobPosting(url="https://x.com/cog/3", title="Lead ML Engineer", company="Cognizant")]
    agent.settings.tracks[1].titles.append("ml engineer")
    agent.settings.tracks[1].queries = ["ml engineer"]
    from unittest import mock
    with mock.patch.object(A.job_apis, "API_SOURCES", ("fake",)), \
            mock.patch.object(A.job_apis, "remotive", lambda *a, **k: posts, create=True):
        agent.settings.search["sources"] = ["remotive"]
        with mock.patch.object(A.job_apis, "API_SOURCES", ("remotive",)):
            new = agent.discover(None, ["remotive"], agent.settings.tracks, 1, HuntSummary())
    titles = [st.get(i)["title"] for i in new]
    assert titles == ["Lead ML Engineer"]


def test_old_cv_names_get_recruiter_copy(agent, tmp_path):
    out = agent.settings.root / "output" / "cvs"
    out.mkdir(parents=True)
    old = out / "base-data_engineering.pdf"
    old.write_bytes(b"%PDF")
    jid, _ = agent.store.upsert_job("https://x.com/n1", title="Data Engineer", company="Acme")
    agent.store.update_job(jid, cv_pdf=str(old), track="data_engineering")
    agent._recruiter_file_name(jid, agent.settings.track("data_engineering"))
    new = agent.store.get(jid)["cv_pdf"]
    assert new.endswith("TestPerson_DE_Base.pdf") and Path(new).exists()


def test_new_ats_fetchers(monkeypatch):
    from applyloop.sources import ats_api
    def fake_get(url, timeout=30):
        if "smartrecruiters" in url and url.endswith("limit=100"):
            return {"content": [{"id": "77", "name": "Senior Data Engineer", "location": {"city": "Atlanta", "region": "GA", "remote": True},
                                 "releasedDate": "2026-10-05"}]}
        if "smartrecruiters" in url:
            return {"jobAd": {"sections": {"jobDescription": {"text": "<p>Spark</p>"}}}}
        if "recruitee" in url:
            return {"offers": [{"title": "ML Engineer", "careers_url": "https://acme.recruitee.com/o/ml", "location": "Remote", "remote": True}]}
        raise AssertionError(url)
    monkeypatch.setattr(ats_api, "_get", fake_get)
    monkeypatch.setattr(ats_api, "_post", lambda url, payload, timeout=30: {"results": [{"title": "Data Engineer", "shortcode": "ABC1", "remote": True}]})
    sr = ats_api.smartrecruiters("acme", "Acme")
    assert sr[0].apply_url == "https://jobs.smartrecruiters.com/acme/77" and "Remote" in sr[0].location and sr[0].description == "Spark"
    assert ats_api.recruitee("acme", "Acme")[0].title == "ML Engineer"
    assert ats_api.workable("acme", "Acme")[0].apply_url == "https://apply.workable.com/acme/j/ABC1/apply/"


def test_old_employer_posting_dropped(agent, monkeypatch):
    from applyloop.sources import JobPosting, ats_api
    agent.settings.search["posted_within_days"] = 7
    st = agent.store
    jid, _ = st.upsert_job("https://www.linkedin.com/jobs/view/1/", title="Data Scientist", company="Acme Health",
                           source="linkedin-alert")
    st.update_job(jid, score=4.2, track="ml_engineering", cv_pdf=__file__)
    st.set_status(jid, S.QUEUED, "ready")
    monkeypatch.setattr(ats_api, "find_board", lambda c: [JobPosting(
        url="https://boards.greenhouse.io/acme/jobs/9", title="Data Scientist", company=c, source="greenhouse",
        apply_url="https://boards.greenhouse.io/embed/job_app?for=acme&token=9", posted="2026-08-01T00:00:00Z")])
    assert agent.apply_one(object(), jid, "queue") == S.FILTERED
    assert "employer's site" in st.get(jid)["status_detail"]


def test_remote_flag_applies_only_remote(agent, monkeypatch):
    st = agent.store
    r, _ = st.upsert_job("https://boards.greenhouse.io/a/jobs/11", title="Data Engineer", company="R",
                         location="Remote - US")
    o, _ = st.upsert_job("https://boards.greenhouse.io/b/jobs/12", title="Data Engineer", company="O",
                         location="Atlanta, GA", description="This role is on-site in our Atlanta office.")
    for j in (r, o):
        st.update_job(j, score=4.5)
        st.set_status(j, S.QUEUED, "ready")
    seen = []
    monkeypatch.setattr(agent, "apply_one", lambda ctx, jid, mode, manual=False: seen.append(jid) or S.NEEDS_INPUT)
    agent.settings.search["remote_run"] = True
    agent._autopilot(None, {"min_score": 4.0}, None, HuntSummary())
    assert seen == [r] and st.get(o)["status"] == S.QUEUED      # Atlanta job waits for a normal run
    # submit --manual --remote walks only remote jobs
    for j in (r, o):
        st.set_status(j, S.MANUAL, "apply yourself")
    seen.clear()
    agent.submit(manual=True)
    assert seen == [r]
    agent.settings.search["remote_run"] = False
    seen.clear()
    agent.submit(manual=True)
    assert sorted(seen) == sorted([r, o])


def test_paused_track_and_prefer_remote(agent, monkeypatch):
    from applyloop.settings import Track
    de, ml = Track("data_engineering"), Track("ml_engineering", paused=True)
    agent.settings.all_tracks = [de, ml]
    agent.settings.tracks = [de]
    agent.settings.search["prefer_remote"] = True
    st = agent.store
    ids = {}
    for name, track, loc, sc in (("onsite", "data_engineering", "Atlanta, GA", 4.9),
                                 ("remote", "data_engineering", "Remote", 4.0),
                                 ("ml", "ml_engineering", "Remote", 5.0)):
        jid, _ = st.upsert_job(f"https://boards.greenhouse.io/{name}/jobs/1", title=f"{name} Data Engineer",
                               company=name, location=loc)
        st.update_job(jid, score=sc, track=track)
        st.set_status(jid, S.QUEUED, "ready")
        ids[name] = jid
    seen = []
    monkeypatch.setattr(agent, "apply_one", lambda ctx, jid, mode, manual=False: seen.append(jid) or S.NEEDS_INPUT)
    agent._autopilot(None, {"min_score": 3.5}, None, HuntSummary())
    assert seen == [ids["remote"], ids["onsite"]]                 # remote first; paused ML track waits
    assert st.get(ids["ml"])["status"] == S.QUEUED
    assert agent.settings.track("ml_engineering") is ml and not agent.settings.is_active("ml_engineering")
    assert [t.id for t in agent._tracks(["ml_engineering"])] == ["ml_engineering"]


def test_paused_track_runs_with_t_and_restores_dropped_jobs(agent):
    from applyloop.settings import Track
    de = Track("data_engineering", titles=["data engineer"])
    ml = Track("ml_engineering", titles=["machine learning engineer", "data scientist"], paused=True)
    agent.settings.all_tracks, agent.settings.tracks = [de, ml], [de]
    st = agent.store
    jid, _ = st.upsert_job("https://boards.greenhouse.io/r/jobs/9", title="Machine Learning Engineer", company="Reddit")
    st.set_status(jid, S.FILTERED, "title doesn't match any track")
    agent._extra_tracks = []
    assert agent._restore_title_filtered() == 0                     # ML still paused, not asked for
    agent._extra_tracks = [t for t in agent._tracks(["ml_engineering"]) if t.paused]
    assert [t.id for t in agent.run_tracks] == ["data_engineering", "ml_engineering"]
    assert agent._restore_title_filtered() == 1 and st.get(jid)["status"] == S.FOUND
    from applyloop.router import route
    assert route("Senior Data Scientist", "", agent.run_tracks).track.id == "ml_engineering"


def test_max_age_filters_without_dropping(agent, monkeypatch):
    from datetime import datetime, timedelta
    st = agent.store
    agent.settings.search["posted_within_days"] = 7
    old = (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d")
    fresh = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    ids = {}
    for name, posted, status in (("old", old, S.MANUAL), ("new", fresh, S.MANUAL), ("unknown", "", S.MANUAL),
                                 ("oldq", old, S.QUEUED)):
        jid, _ = st.upsert_job(f"https://boards.greenhouse.io/{name}/jobs/1", title="Data Engineer",
                               company=name, posted=posted)
        st.update_job(jid, score=4.5)
        st.set_status(jid, status, "x")
        ids[name] = jid
    seen = []
    monkeypatch.setattr(agent, "apply_one", lambda ctx, jid, mode, manual=False: seen.append(jid) or S.NEEDS_INPUT)
    agent.submit(manual=True)                                           # no --max-age → everything
    assert sorted(seen) == sorted([ids["old"], ids["new"], ids["unknown"]])
    seen.clear()
    agent.submit(manual=True, max_age=3)
    assert sorted(seen) == sorted([ids["new"], ids["unknown"]])         # unknown date = found just now
    assert st.get(ids["old"])["status"] == S.MANUAL                     # still on the list
    seen.clear()
    agent._autopilot(None, {"min_score": 4.0}, None, HuntSummary())
    assert ids["oldq"] not in seen and st.get(ids["oldq"])["status"] == S.QUEUED


def test_already_applied_under_company_name_variant(agent, monkeypatch):
    from applyloop.urls import same_company
    assert same_company("AIG", "AIG PC Global Services, Inc.")
    assert same_company("Intercontinental Exchange", "Intercontinental Exchange Holdings, Inc.")
    assert not same_company("Apple", "Applebee's") and not same_company("Meta", "Metaview")
    st = agent.store
    a, _ = st.upsert_job("https://www.glassdoor.com/job/1", title="Data Engineer, Technology Risk",
                         company="Intercontinental Exchange")
    st.set_status(a, S.APPLIED, "submitted by you")
    b, _ = st.upsert_job("https://www.indeed.com/viewjob?jk=2", title="Data Engineer, Technology Risk",
                         company="Intercontinental Exchange Holdings, Inc.")
    st.update_job(b, score=4.5, cv_pdf="x.pdf")
    st.set_status(b, S.MANUAL, "apply yourself")
    opened = []
    monkeypatch.setattr("applyloop.apply_engine.engine.manual_apply", lambda *a, **k: opened.append(1) or S.APPLIED)
    assert agent.apply_one(None, b, "queue", manual=True) == S.SKIPPED
    assert not opened and st.get(b)["status_detail"] == f"already applied as #{a}"


def test_cv_rebuilt_when_base_cv_changes(tmp_path):
    import os, time
    from applyloop.agent import Agent
    from applyloop.settings import Track
    base = tmp_path / "de.pdf"; base.write_text("v1")
    made = tmp_path / "job.pdf"; made.write_text("cv")
    old = time.time() - 100
    os.utime(base, (old, old))
    os.utime(made, (old + 50, old + 50))
    tr = Track("data_engineering", fallback_pdf=base)
    assert Agent._cv_outdated({"cv_pdf": str(made)}, tr) is False
    base.write_text("v2")                                   # you updated your CV
    assert Agent._cv_outdated({"cv_pdf": str(made)}, tr) is True
    assert Agent._cv_outdated({"cv_pdf": str(tmp_path / "gone.pdf")}, tr) is True
