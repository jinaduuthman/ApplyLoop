"""Unit tests for the job-search agent core (no network, no browser)."""

from pathlib import Path

import pytest

from applyloop import store as S
from applyloop.answers import AnswerBank, pick_option
from applyloop.filters import filter_reason, parse_salary_max
from applyloop.latex_cv import TexCV, escape_specials, numbers_in
from applyloop.llm import extract_json
from applyloop.models import CandidateProfile
from applyloop.router import route
from applyloop.settings import Track
from applyloop.tailor_tex import keyword_gap, validate_bullet
from applyloop.urls import canonical_url, fingerprint

TEX = r"""\documentclass{article}
\usepackage{tabularx,ragged2e}
\newcolumntype{S}{>{\bfseries\RaggedRight\arraybackslash}p{0.25\textwidth}}
\newcolumntype{Y}{>{\RaggedRight\arraybackslash}X}
\newcommand{\header}[1]{\par\textsc{#1}\par}
\begin{document}
\header{\bf{Summary}}
{\small
Data engineer building pipelines with Spark and Airflow.
}
\header{\bf{Technical Skills}}
{\small
\begin{tabularx}{\textwidth}{S Y}
Orchestration \& ELT: & Airflow, dbt. \\
Cloud Platforms: & AWS (S3, EMR). \\
\end{tabularx}
}
\header{\bf{Experience}}
{\small
\begin{itemize}
\item {\bf Data Engineer, Acme} \hfill 2020 -- Present \\
\begin{itemize}
    \item Built Spark jobs cutting runtime 50\% across 3 teams.
    \item Ran Airflow DAGs for reporting.
    \item Wrote Terraform for AWS.
\end{itemize}
\end{itemize}
}
\header{\bf{Publications}}
\begin{itemize}
    \item A paper. ACL 2024.
\end{itemize}
\end{document}
"""

OTHER = TEX.replace("Airflow, dbt.", "Airflow, dbt, Kafka, Dagster.").replace("Terraform for AWS", "Kubernetes on AWS")


def test_canonical_urls():
    assert canonical_url("https://www.indeed.com/rc/clk?jk=abc&fccid=x&vjs=3") == "https://www.indeed.com/viewjob?jk=abc"
    assert canonical_url("https://indeed.com/viewjob?jk=abc&from=serp") == "https://www.indeed.com/viewjob?jk=abc"
    assert canonical_url("https://www.linkedin.com/jobs/view/data-eng-at-x-4395077470?refId=1&trackingId=2") == \
        "https://www.linkedin.com/jobs/view/4395077470"
    assert "jl=1009" in canonical_url("https://www.glassdoor.com/job-listing/x.htm?jl=1009&src=GD")
    a = canonical_url("https://boards.greenhouse.io/acme/jobs/123?gh_src=abc&utm_source=li")
    assert a == "https://boards.greenhouse.io/acme/jobs/123"


def test_fingerprint_cross_site():
    assert fingerprint("Acme Inc.", "Senior Data Engineer", "Atlanta, GA") == \
        fingerprint("ACME", "Senior Data Engineer", "Atlanta, GA (Hybrid)")


def test_store_dedup_and_status():
    st = S.Store(":memory:")
    i1, new1 = st.upsert_job("https://www.indeed.com/rc/clk?jk=abc&x=1", title="Data Engineer", company="Acme",
                             location="Atlanta, GA", source="indeed")
    i2, new2 = st.upsert_job("https://www.indeed.com/viewjob?jk=abc", title="Data Engineer", company="Acme")
    i3, new3 = st.upsert_job("https://www.glassdoor.com/job-listing/?jl=99", title="Data Engineer",
                             company="Acme Inc", location="Atlanta", source="glassdoor")
    assert (new1, new2, new3) == (True, False, False) and i1 == i2 == i3
    st.set_status(i1, S.APPLIED, "confirmed")
    assert st.get(i1)["applied_at"] and st.submitted_on() == 1
    assert [e["status"] for e in st.events(i1)] == ["found", "applied"]
    with pytest.raises(ValueError):
        st.set_status(i1, "bogus")


def test_filters():
    f = {"title_exclude": ["intern", "junior"], "company_blocklist": ["Initech"],
         "description_exclude": [r"security clearance"], "min_salary": 120000}
    assert filter_reason({"title": "Data Engineering Intern"}, f)
    assert filter_reason({"title": "Internal Tools Engineer"}, f) is None       # whole words
    assert filter_reason({"title": "DE", "company": "initech"}, f)
    assert filter_reason({"title": "DE", "description": "Requires active Security Clearance"}, f)
    assert filter_reason({"title": "DE", "salary": "$90,000 - $110,000 a year"}, f)
    assert filter_reason({"title": "DE", "salary": "$70 - $80 an hour"}, f) is None   # 166k/yr
    assert parse_salary_max("$140K–$180K") == 180000


def tracks():
    return [Track("data_engineering", titles=["data engineer"], keywords=["spark", "airflow", "dbt", "snowflake"]),
            Track("ml_engineering", titles=["machine learning engineer", "ml engineer"], keywords=["pytorch", "llm"]),
            Track("research_scientist", titles=["research scientist", "applied scientist"], keywords=["phd", "nlp"])]


def test_router():
    assert route("Senior Data Engineer", "spark airflow dbt", tracks()).track.id == "data_engineering"
    assert route("Applied Scientist, NLP", "phd nlp publications", tracks()).track.id == "research_scientist"
    assert route("Staff ML Engineer", "pytorch llm serving", tracks()).track.id == "ml_engineering"
    assert route("Barista", "coffee", tracks()).track is None


def test_answers_whole_word_and_eeo(tmp_path):
    prof = CandidateProfile(full_name="Ada Lovelace", email="ada@x.com", city="Atlanta", country="US",
                            target_comp="$150K")
    bank = AnswerBank(prof, rules=[{"match": ["require (visa )?sponsorship"], "answer": "No"},
                                   {"match": ["authori[sz]ed to work"], "answer": "Yes"}])
    assert bank.resolve("What is your ethnicity?", options=["A", "B", "Prefer not to say"]).value == "Prefer not to say"
    assert bank.resolve("Gender", options=["Male", "Female"]).value == ""       # no decline option → blank
    assert bank.resolve("Describe how you separate concerns").source == "none"
    assert bank.resolve("Are you authorized to work in this country?", options=["Yes", "No"]).value == "Yes"
    assert bank.resolve("Do you require sponsorship?", options=["Yes", "No"]).value == "No"
    assert bank.resolve("First Name").value == "Ada" and bank.resolve("Last Name").value == "Lovelace"
    assert pick_option("7", ["0-2", "3-5", "6-10", "10+"]) == "6-10"
    assert pick_option("12", ["1", "5+", "10+"]) == "10+"


def test_tex_parse_and_edit(tmp_path):
    cv = TexCV.parse(TEX)
    assert "Spark" in cv.summary
    assert [s.label for s in cv.skills] == [r"Orchestration \& ELT", "Cloud Platforms"]
    assert len(cv.roles) == 1 and len(cv.roles[0].bullets) == 3        # publications not included
    cv.reorder("r0", ["b2", "b0", "b1"])
    cv.set_bullet("b1", "Ran 50% of Airflow DAGs & reports")
    src = cv.source()
    assert src.index("Terraform") < src.index("Built Spark")
    assert r"50\% of Airflow DAGs \& reports" in src
    assert escape_specials(r"cut 35\% and 20%") == r"cut 35\% and 20\%"
    assert numbers_in(r"cut 7.6\,ms by 9$\times$") == {"7.6", "9"}


def test_gap_only_surfaces_true_skills():
    base, other = TexCV.parse(TEX), TexCV.parse(OTHER)
    jd = "We use Kafka, Spark, Airflow, Flink and Kubernetes."
    g = keyword_gap(jd, base, [base, other])
    assert "kafka" in g.have_elsewhere and "kubernetes" in g.have_elsewhere
    assert "flink" in g.lacking and "flink" not in g.have_elsewhere
    assert "spark" in g.in_base


def test_validate_bullet_guards():
    old = r"Built Spark jobs cutting runtime 50\% across 3 teams."
    assert validate_bullet(old, r"Built Spark and Kafka jobs cutting runtime 50\% across 3 teams.", []) is None
    assert "numbers" in validate_bullet(old, "Built Spark jobs cutting runtime 60% across 3 teams.", [])
    assert "flink" in validate_bullet(old, r"Built Flink jobs cutting runtime 50\% across 3 teams.", ["flink"])
    assert validate_bullet(old, old + " " * 0 + "x" * 80, []) == "too long"


def test_extract_json():
    assert extract_json('Sure! ```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}


def test_eeo_yaml_answers():
    from applyloop.eeo import EEO
    e = EEO({"gender": ["Male", "Man"], "race": ["Black or African American", "Black"],
             "veteran": ["I am not a protected veteran", "Not applicable"],
             "disability": ["No, I do not have a disability", "No"]})
    assert e.answer("Gender", ["Female", "Male", "Decline to self-identify"]) == ("Male", "eeo")
    assert e.answer("Gender", ["Woman", "Man", "Non-binary"]) == ("Man", "eeo")      # never "Female"/"Woman"
    assert e.answer("Race/Ethnicity", ["White", "Black or African American", "Asian"])[0] == "Black or African American"
    assert e.answer("Race", ["White", "Black/African American", "Two or more"])[0] == "Black/African American"
    assert e.answer("Are you Hispanic or Latino?", ["Yes", "No", "Decline to answer"]) == ("Decline to answer", "eeo")
    assert e.answer("Veteran status", ["I identify as a protected veteran", "I am not a protected veteran",
                                        "I don't wish to answer"])[0] == "I am not a protected veteran"
    assert e.answer("Disability status", ["Yes, I have a disability", "No, I do not have a disability",
                                           "I do not want to answer"])[0] == "No, I do not have a disability"
    assert e.answer("Pronouns", ["he/him", "Prefer not to say"]) == ("Prefer not to say", "eeo")
    assert e.answer("Years of Python", []) is None


def test_cover_letter_only_when_required():
    from applyloop.apply_engine.forms import FormFiller
    called = []
    f = FormFiller(AnswerBank(CandidateProfile()), None, cover_text=lambda: called.append(1) or "letter",
                   cover_policy="required")
    out = f._fill_field(None, {"id": "x", "type": "textarea", "label": "Cover Letter", "required": False})
    assert out.source == "skipped" and not called


def test_posted_age_and_work_mode_filters():
    from datetime import datetime, timedelta
    from applyloop.filters import detect_work_mode, parse_posted_days, wanted_modes
    now = datetime.now()
    seen = (now - timedelta(days=2)).isoformat(timespec="seconds")
    assert parse_posted_days("Posted 3 days ago", seen) == pytest.approx(5, abs=0.01)
    assert parse_posted_days("30+ days ago") == 30 and parse_posted_days("Just posted") == 0
    assert parse_posted_days("24h") == 1 and parse_posted_days("") is None
    search = {"posted_within_days": 7, "remote_only": True}
    assert "posted" in filter_reason({"title": "DE", "posted": "14 days ago"}, {}, search=search)
    assert filter_reason({"title": "DE", "posted": "2d", "location": "Remote"}, {}, search=search) is None
    assert "hybrid" in filter_reason({"title": "DE", "location": "Hybrid work in Atlanta, GA"}, {}, search=search)
    assert "onsite" in filter_reason({"title": "DE", "location": "Atlanta, GA",
                                      "description": "This role is not remote."}, {}, search=search)
    assert detect_work_mode("DE", "Atlanta, GA", "We are a remote-first company") == "remote"
    assert wanted_modes({"work_mode": ["remote", "hybrid"]}) == {"remote", "hybrid"}
    assert wanted_modes({"work_mode": "any"}) == set()


def test_provider_profiles(monkeypatch):
    from applyloop.llm import resolve_config
    for k in ("APPLYLOOP_PROVIDER", "APPLYLOOP_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    cfg = {"use": "openai", "providers": {
        "openai": {"model": "luna", "tasks": {"tailor": "terra"}},
        "gemini": {"model": "flash-lite", "tasks": {"tailor": "flash"}}}}
    assert resolve_config(cfg, "score").model == "luna"
    assert resolve_config(cfg, "tailor").model == "terra"
    g = resolve_config(cfg, "tailor", provider="gemini")
    assert (g.provider, g.model, g.api_key) == ("gemini", "flash", "g")
    monkeypatch.setenv("APPLYLOOP_PROVIDER", "gemini")
    assert resolve_config(cfg).model == "flash-lite"


def test_api_sources_parse(monkeypatch):
    from applyloop.sources import job_apis
    monkeypatch.setenv("ADZUNA_APP_ID", "i")
    monkeypatch.setenv("ADZUNA_APP_KEY", "k")
    calls = []

    def fake_get(url, timeout=30):
        calls.append(url)
        if "adzuna" in url:
            return {"results": [{"redirect_url": "https://www.adzuna.com/land/ad/1", "title": "<strong>Senior</strong> Data Engineer",
                                 "company": {"display_name": "Acme"}, "location": {"display_name": "Atlanta, GA"},
                                 "salary_min": 140000, "salary_max": 170000, "created": "2026-10-01T00:00:00Z",
                                 "description": "Spark and Kafka..."}]}
        return {"jobs": [{"url": "https://remotive.com/j/2", "title": "ML Engineer", "company_name": "Beta",
                          "candidate_required_location": "USA", "publication_date": "2026-10-02T00:00:00",
                          "description": "<p>PyTorch</p>", "salary": ""}]}

    monkeypatch.setattr(job_apis, "_get", fake_get)
    a = job_apis.adzuna("Senior Data Engineer", "Atlanta, GA", 7, 10)
    assert a[0].title == "Senior Data Engineer" and a[0].company == "Acme"
    assert a[0].salary == "$140,000 - $170,000 a year" and "max_days_old=7" in calls[0]
    r = job_apis.remotive("ML Engineer", days=0)
    assert r[0].location == "Remote (USA)" and r[0].description == "PyTorch"


def test_parse_indeed_alert_email():
    from applyloop.sources.email_alerts import parse_alert
    html = """<table><tr><td><a href="https://www.indeed.com/rc/clk/dl?jk=abc123&from=ja">Senior Data Engineer</a><br>
    Acme Corp<br>Atlanta, GA<br>$150,000 - $170,000 a year<br>Build Spark and Kafka pipelines for our analytics platform at scale.</td></tr>
    <tr><td><a href="https://www.indeed.com/viewjob?jk=def456">ML Engineer</a><br>Beta Inc<br>Remote</td></tr>
    <tr><td><a href="https://www.indeed.com/jobs?q=data">View all jobs</a> <a href="https://www.indeed.com/unsub">Unsubscribe</a></td></tr></table>"""
    jobs = parse_alert(html, "indeed")
    assert [j.title for j in jobs] == ["Senior Data Engineer", "ML Engineer"]
    assert jobs[0].company == "Acme Corp" and jobs[0].location == "Atlanta, GA"
    assert jobs[0].salary.startswith("$150,000") and jobs[0].source == "indeed-alert"
    assert jobs[1].company == "Beta Inc" and jobs[1].location == "Remote"


def test_country_aliases_and_list_answers():
    prof = CandidateProfile(country="United States")
    bank = AnswerBank(prof, rules=[{"match": ["currently reside"],
                                    "answer": ["United States", "United States of America", "USA", "US"]}])
    assert bank.resolve("Country where you currently reside", options=["Australia", "USA"]).value == "USA"
    assert bank.resolve("Country where you currently reside", options=["Australia", "US"]).value == "US"
    assert bank.resolve("Country", options=["Austria", "US", "UK"]).value == "US"      # profile alias
    assert pick_option("US", ["Australia", "Belarus"]) == ""                         # no substring hits


def test_degree_per_track_list():
    from applyloop.answers import AnswerBank
    from applyloop.models import CandidateProfile
    rules = [{"match": ["highest (level of )?(education|degree) (completed|obtained|earned)"], "answer": "Master's"},
             {"match": ["^degree$", "highest (level of )?education"], "answer": "{highest_degree}"}]
    d = {"highest_degree": {"ml_engineering": ["Doctor of Philosophy (Ph.D.)", "PhD", "Doctorate"],
                            "default": ["Master's Degree", "Master's", "Masters"]}}
    opts = ["Bachelor's Degree", "Master's Degree", "Doctor of Philosophy (Ph.D.)"]
    ml = AnswerBank(CandidateProfile(), rules=rules, defaults=d, track_id="ml_engineering")
    de = AnswerBank(CandidateProfile(), rules=rules, defaults=d, track_id="data_engineering")
    assert ml.resolve("Degree", "text", opts, allow_ai=False).value == "Doctor of Philosophy (Ph.D.)"
    assert ml.resolve("Degree", "text", ["Masters", "Doctorate"], allow_ai=False).value == "Doctorate"
    assert de.resolve("Degree", "text", opts, allow_ai=False).value == "Master's Degree"
    assert ml.resolve("Highest level of education completed", "text", opts, allow_ai=False).value == "Master's Degree"


def test_location_filter_atlanta_or_remote():
    from applyloop.filters import location_reason
    s = {"allowed_locations": ["Atlanta", "Alpharetta"]}
    keep = [("Atlanta, GA", ""), ("Alpharetta, Georgia (Hybrid)", ""), ("Remote - USA", ""), ("United States", ""),
            ("", ""), ("New York, NY", "This is a fully remote position.")]
    drop = [("San Francisco", ""), ("Remote - United Kingdom", ""), ("Bangalore - Remote", ""),
            ("New York, NY", "Hybrid, 3 days in office")]
    for loc, d in keep:
        assert location_reason({"title": "Data Engineer", "location": loc, "description": d}, s) is None, loc
    for loc, d in drop:
        assert location_reason({"title": "Data Engineer", "location": loc, "description": d}, s), loc
    assert location_reason({"title": "x", "location": "San Francisco"}, {}) is None


def test_staffing_filter():
    from applyloop.filters import staffing_reason
    f = {"exclude_staffing": True}
    assert staffing_reason({"company": "Insight Global", "title": "GCP Data Engineer"}, f)
    assert staffing_reason({"company": "CYNET SYSTEMS", "title": "Data Engineer"}, f)
    assert staffing_reason({"company": "Acme Staffing LLC", "title": "Data Engineer"}, f)
    assert staffing_reason({"company": "Acme", "title": "Data Engineer - W2 only"}, f)
    assert staffing_reason({"company": "Acme", "title": "DE", "description": "Our client, a Fortune 500 bank, is seeking"}, f)
    assert staffing_reason({"company": "Airbnb", "title": "Senior Data Scientist", "description": "Join our team"}, f) is None
    assert staffing_reason({"company": "Ramp", "title": "Talent Partner"}, f) is None   # title, not company
    assert staffing_reason({"company": "Insight Global", "title": "x"}, {}) is None      # off by default


def test_most_specific_rule_wins():
    from applyloop.answers import AnswerBank
    from applyloop.models import CandidateProfile
    rules = [{"match": ["authori[sz]ed to work", "right to work"], "answer": "Yes"},      # general first
             {"match": ["require (visa )?sponsorship"], "answer": "No"}]
    b = AnswerBank(CandidateProfile(), rules=rules)
    q = "Will you now or in the future require sponsorship to extend your right to work in your current location?"
    assert b.resolve(q, "yesno", ["Yes", "No"], allow_ai=False).value == "No"
    assert b.resolve("Do you have the right to work in the US?", "yesno", ["Yes", "No"], allow_ai=False).value == "Yes"


def test_alert_parsers():
    from applyloop.filters import staffing_reason
    from applyloop.sources.email_alerts import parse_alert
    gd = ('<a href="https://www.glassdoor.com/partner/jobListing.htm?pos=101&jobListingId=111&x=1">'
          '<span>Amazon</span><span>&nbsp;<!-- -->3.6 ★</span><td>Applied Scientist, DESA</td>'
          '<td>Seattle, WA</td><span>$143K - $193K</span><span>(</span><span>Employer est.</span>'
          '<span>)</span><span>4d</span></a>'
          '<a href="https://www.glassdoor.com/partner/jobListing.htm?pos=102&jobListingId=222">'
          '<span>AiWhiz Solutions</span><td>Senior Big Data Engineer</td><td>Remote</td>'
          '<span>Easy Apply</span><span>1d</span></a>'
          '<a href="https://www.glassdoor.com/partner/jobListing.htm?pos=101&jobListingId=111&dup=1"><span>Amazon</span>'
          '<td>Applied Scientist, DESA</td><td>Seattle, WA</td></a>')
    j = parse_alert(gd, "glassdoor")
    assert [(x.company, x.title, x.location, x.posted, x.easy_apply) for x in j] == [
        ("Amazon", "Applied Scientist, DESA", "Seattle, WA", "4d", False),
        ("AiWhiz Solutions", "Senior Big Data Engineer", "Remote", "1d", True)]
    assert j[0].salary.startswith("$143K")
    li = ('<a href="https://www.linkedin.com/comm/jobs/view/4473389845/?trackingId=x"></a>'
          '<a href="https://www.linkedin.com/comm/jobs/view/4473389845/?trackingId=x">Research Scientist, Google Research</a>'
          '<p>Google · Mountain View, CA (On-site)</p><p>1 connection</p>'
          '<a href="https://www.linkedin.com/comm/jobs/view/4377957379/?t=y">Ph.D. Research Data Scientist, GenAI</a>'
          '<p>AIG · Atlanta, GA</p><p>Actively recruiting</p>')
    j = parse_alert(li, "linkedin")
    assert [(x.company, x.title, x.location) for x in j] == [
        ("Google", "Research Scientist, Google Research", "Mountain View, CA (On-site)"),
        ("AIG", "Ph.D. Research Data Scientist, GenAI", "Atlanta, GA")]
    assert j[0].url == "https://www.linkedin.com/jobs/view/4473389845/"
    assert parse_alert('<a href="https://apply.indeed.com/indeedapply/confirmemail/viewjob?x=1">Senior DE</a>', "indeed") == []
    assert staffing_reason({"company": "Bolden", "title": "AI Engineer", "salary": "$50 - $60 (Employer est.)"},
                           {"exclude_staffing": True})
    assert staffing_reason({"company": "FIS", "title": "DE", "salary": "$109K - $142K"}, {"exclude_staffing": True}) is None


def test_cv_file_names(tmp_path):
    from applyloop.settings import Track
    from applyloop.tailor_tex import person_name
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "profile.yml").write_text("candidate:\n  full_name: Jane Smith\n")
    assert person_name(tmp_path) == "JaneSmith"
    assert Track("data_engineering").tag == "DE" and Track("ml_engineering").tag == "MLE"
    assert Track("research_scientist").tag == "ResearchScientist"
    assert Track("product_manager").tag == "PM" and Track("x", short="Quant").tag == "Quant"


def test_security_clearance_filter():
    from applyloop.filters import clearance_reason
    yes = ["Must hold an active TS/SCI with polygraph", "Secret clearance required", "Public Trust",
           "Ability to obtain and maintain a security clearance"]
    no = ["No security clearance required", "Use AWS Secrets Manager", "Clearance is not required"]
    assert all(clearance_reason({"title": "Data Engineer", "description": d}, {}) for d in yes)
    assert not any(clearance_reason({"title": "Data Engineer", "description": d}, {}) for d in no)
    assert clearance_reason({"title": "Data Engineer (TS/SCI)"}, {})
    assert filter_reason({"title": "Data Engineer", "description": "Active Secret clearance"}, {}) \
        .startswith("security clearance")
    assert clearance_reason({"title": "x", "description": "TS/SCI"}, {"exclude_clearance": False}) is None


def test_w2_postings_dropped():
    from applyloop.filters import staffing_reason
    f = {"exclude_staffing": True}
    assert "W2" in staffing_reason({"title": "Google Cloud Platform Data Engineer-W2", "company": "Acme"}, f)
    assert staffing_reason({"title": "Data Engineer", "company": "Acme",
                            "description": "This is a 12 month contract on W2 with our client."}, f)
    assert staffing_reason({"title": "AI / ML Engineer | Remote W2 Role", "company": "Acme"}, f)
    assert staffing_reason({"title": "Data Engineer", "company": "Acme",
                            "description": "Payroll team: W-2 forms and year-end processing."}, f) is None
    assert staffing_reason({"title": "Data Engineer (W2)", "company": "Acme"},
                           {"exclude_staffing": True, "exclude_w2": False}) is None


def test_teal_listing_parser():
    from applyloop.sources import teal
    page = """<html><body><div class="card"><a href="/job/sr-data-engineer_7ea1a0a690a841cf6e452b79df4efbb8c1b96">
      <h3>Sr. Data Engineer</h3></a><div>UMB Bank</div><div>Kansas City, MO</div><span>10h</span></div>
      <div class="card"><a href="/job/data-engineer_7ea1adc4768ab4f12a575a4d1ffb12d41214f">Data Engineer</a>
      <span>New Job</span><div>Acme Analytics</div><div>Remote</div><div>$140K - $170K/yr</div><span>1d</span></div>
      <a href="/jobs/title/data-engineer?page=2">2</a></body></html>"""
    jobs = teal.parse_listing(page)
    assert [(j.title, j.company, j.location, j.posted) for j in jobs] == [
        ("Sr. Data Engineer", "UMB Bank", "Kansas City, MO", "10h"),
        ("Data Engineer", "Acme Analytics", "Remote", "1d")]
    assert jobs[1].salary.startswith("$140K") and jobs[0].url.startswith("https://www.tealhq.com/job/")
    nxt = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"jobs":[{"title":"AI Engineer",'
           '"company":{"name":"Globex"},"location":"Remote","slug":"ai-engineer_abc123","postedAt":"2026-10-18"}]}}}</script>')
    j = teal.parse_listing(nxt)[0]
    assert (j.title, j.company, j.url) == ("AI Engineer", "Globex", "https://www.tealhq.com/job/ai-engineer_abc123")
    from applyloop.apply_engine.engine import automation_verdict
    assert automation_verdict(jobs[0].url)[0] is False          # listing site → found on the employer's site


def test_remote_but_really_europe():
    from applyloop.filters import location_reason
    s = {"allowed_locations": ["Atlanta"], "remote_us_only": True}
    assert location_reason({"title": "AI Engineer", "company": "Inetum Polska", "location": "Remote"}, s)
    assert location_reason({"title": "DE", "company": "Qualco Group", "location": "Remote",
                            "description": "Fully remote within Greece or the EU. Salary €60,000"}, s)
    assert location_reason({"title": "DE", "company": "Stripe", "location": "Remote",
                            "description": "Customers across the US and Europe. Remote in the United States."}, s) is None
    assert location_reason({"title": "DE", "company": "Acme", "location": "Remote - US",
                            "description": "remote within the EU also possible"}, s) is None
    from applyloop.sources import teal
    page = ('<a href="/job/senior-ai-engineer_abc">Senior AI Engineer</a><div>Inetum Polska</div>'
            '<div>2d • Remote</div><a href="/job/big-data-engineer_def">Big Data Engineer</a>'
            '<div>Xperteez Technology • ,</div><div>34d • Remote</div>')
    jobs = teal.parse_listing(page)
    assert [(j.company, j.location, j.posted) for j in jobs] == [
        ("Inetum Polska", "Remote", "2d"), ("Xperteez Technology", "Remote", "34d")]


def test_ats_search_discovery(monkeypatch, tmp_path):
    from applyloop.sources import ats_search
    assert ats_search.board_of("https://job-boards.greenhouse.io/acme/jobs/123") == ("greenhouse", "acme")
    assert ats_search.board_of("https://boards.greenhouse.io/embed/job_app?for=globex&token=9") == ("greenhouse", "globex")
    assert ats_search.board_of("https://jobs.lever.co/plaid/5a1b") == ("lever", "plaid")
    assert ats_search.board_of("https://jobs.ashbyhq.com/ramp/abc") == ("ashby", "ramp")
    assert ats_search.board_of("https://apply.workable.com/initech/j/AB12/") == ("workable", "initech")
    assert ats_search.company_from_result("greenhouse", "Job Application for Data Engineer at Acme Inc", "acme") == "Acme Inc"
    assert ats_search.company_from_result("lever", "Plaid - Senior Data Engineer", "plaid") == "Plaid"
    assert ats_search.company_from_result("ashby", "AI Engineer @ Ramp", "ramp") == "Ramp"
    calls = []
    def fake(q, days, count=20):
        calls.append(q)
        if "greenhouse" in q:
            return [{"url": "https://job-boards.greenhouse.io/acme/jobs/1", "title": "Job Application for Data Engineer at Acme", "snippet": ""},
                    {"url": "https://job-boards.greenhouse.io/stripe/jobs/2", "title": "x at Stripe", "snippet": ""}]
        if "ycombinator" in q:
            return [{"url": "https://www.ycombinator.com/companies/foo-ai/jobs/AbC123-ai-engineer",
                     "title": "AI Engineer at Foo AI | Y Combinator", "snippet": "build agents"}]
        return []
    monkeypatch.setattr(ats_search, "web_search", fake)
    boards, direct = ats_search.discover(["Data Engineer"], 7, ["greenhouse", "lever", "yc"], 6,
                                         known={("greenhouse", "stripe")})
    assert boards == [{"ats": "greenhouse", "slug": "acme", "name": "Acme"}]          # stripe already configured
    assert direct[0].title == "AI Engineer" and direct[0].company == "Foo AI"
    assert "site:job-boards.greenhouse.io \"Data Engineer\"" in calls
    ats_search.remember(tmp_path, boards)
    assert ats_search.load_remembered(tmp_path, 30)[0]["slug"] == "acme"
    from applyloop.apply_engine.engine import automation_verdict
    assert automation_verdict(direct[0].url)[0] is False
