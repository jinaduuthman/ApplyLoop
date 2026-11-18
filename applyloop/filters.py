"""Zero-cost filters applied before any AI call."""

from __future__ import annotations

import re
from datetime import datetime


def _words_re(terms: list[str]) -> re.Pattern | None:
    terms = [t for t in terms if t]
    if not terms:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b", re.I)


def parse_salary_max(text: str) -> float | None:
    """Best-effort annualized max from strings like '$120,000 - $150,000 a year',
    '$60 - $75 an hour', '$140K–$180K'. Returns None if unknown."""
    if not text:
        return None
    t = text.lower().replace(",", "")
    nums = []
    for m in re.finditer(r"\$?\s*(\d+(?:\.\d+)?)\s*(k)?\b", t):
        v = float(m.group(1)) * (1000 if m.group(2) else 1)
        nums.append(v)
    nums = [n for n in nums if n >= 10]
    if not nums:
        return None
    top = max(nums)
    if "hour" in t or "/hr" in t or (top < 300 and "year" not in t):
        top *= 2080
    elif "month" in t:
        top *= 12
    return top


# ── posting age ───────────────────────────────────────────────────────

def parse_posted_days(posted, found_at: str = "", now: datetime | None = None) -> float | None:
    """Age in days from Indeed/Glassdoor/ATS strings: 'Posted 3 days ago', 'Just posted',
    'Today', '30+ days ago', '3d', '24h', ISO dates, epoch ms. Relative strings are measured
    from when applyloop saw the posting (found_at). None = unknown."""
    now = now or datetime.now()
    if posted is None or posted == "":
        return None
    t = str(posted).strip().lower()
    base = now
    if found_at:
        try:
            base_seen = datetime.fromisoformat(found_at[:19])
            extra = (now - base_seen).total_seconds() / 86400
        except ValueError:
            extra = 0.0
    else:
        extra = 0.0
    if t.isdigit() and len(t) >= 12:                                  # epoch ms (Lever)
        dt = datetime.fromtimestamp(int(t) / 1000)
        return max(0.0, (base - dt).total_seconds() / 86400)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", t)                          # ISO date / datetime
    if m:
        try:
            return max(0.0, (base - datetime.fromisoformat(m.group(1))).total_seconds() / 86400)
        except ValueError:
            return None
    if re.search(r"just posted|today|just now|new\b|few (minutes|hours)", t):
        return 0.0 + extra
    m = re.search(r"(\d+)\s*\+?\s*(h|hr|hrs|hour|hours|d|day|days|w|wk|week|weeks|mo|month|months)\b", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n / 24 if unit.startswith("h") else n * 7 if unit.startswith("w") else \
            n * 30 if unit.startswith("mo") else n
        return days + extra
    if "yesterday" in t:
        return 1.0 + extra
    return None


# ── work mode (remote / hybrid / onsite) ──────────────────────────────

_NOT_REMOTE = re.compile(r"\b(not|no|non)[- ]remote\b|\bremote (work )?(is )?not (available|possible|an option)", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_REMOTE_STRONG = re.compile(r"\bremote\b|\bwork from home\b|\bwfh\b|\banywhere in the (us|u\.s\.|united states)\b", re.I)
_REMOTE_DESC = re.compile(r"\b(fully|100%|completely) remote\b|\bremote[- ]first\b|\bremote[- ]friendly\b|"
                          r"\bwork from home\b|\bthis (is a|role is|position is) (a )?remote\b|\bremote (role|position|opportunity)\b", re.I)
_ONSITE = re.compile(r"\bon[- ]?site\b|\bin[- ]office\b|\bin[- ]person\b|\b\d days? (a|per) week in (the )?office\b", re.I)

WORK_MODES = ("remote", "hybrid", "onsite")


def detect_work_mode(title: str, location: str, description: str = "") -> str:
    """Best guess: 'remote' | 'hybrid' | 'onsite' | 'unknown'."""
    head = f"{title} {location}"
    desc = description or ""
    if _NOT_REMOTE.search(head) or _NOT_REMOTE.search(desc[:4000]):
        return "hybrid" if _HYBRID.search(head + " " + desc[:4000]) else "onsite"
    if _HYBRID.search(head):
        return "hybrid"
    if _REMOTE_STRONG.search(head):
        return "remote"
    if _HYBRID.search(desc[:4000]):
        return "hybrid"
    if _REMOTE_DESC.search(desc[:6000]):
        return "remote"
    if _ONSITE.search(head) or _ONSITE.search(desc[:4000]):
        return "onsite"
    return "unknown" if not location else "onsite"


_GENERIC_LOC = re.compile(r"^\s*(united states( of america)?|usa?|u\.s\.a?\.?|anywhere|nationwide|multiple locations|"
                          r"remote|various|north america|americas)?\s*$", re.I)
_NON_US = re.compile(r"\b(united kingdom|uk|england|london|ireland|dublin|india|bangalore|bengaluru|hyderabad|pune|"
                     r"canada|ontario|toronto|vancouver|montreal|germany|berlin|munich|france|paris|netherlands|"
                     r"amsterdam|spain|madrid|barcelona|portugal|lisbon|poland|warsaw|singapore|japan|tokyo|"
                     r"australia|sydney|melbourne|brazil|s[aã]o paulo|mexico|emea|apac|latam|europe|israel|"
                     r"tel aviv|philippines|korea|seoul|china|hong kong|taiwan|dubai|uae|switzerland|zurich|"
                     r"sweden|stockholm|denmark|copenhagen|argentina|colombia|costa rica|romania|czech|"
                     r"czechia|prague|brno|austria|vienna|italy|milan|rome|belgium|brussels|norway|oslo|finland|"
                     r"helsinki|greece|athens|hungary|budapest|serbia|belgrade|bulgaria|sofia|croatia|zagreb|"
                     r"slovakia|bratislava|slovenia|ljubljana|lithuania|vilnius|latvia|riga|estonia|tallinn|"
                     r"ukraine|kyiv|kiev|lviv|luxembourg|iceland|reykjavik|scotland|edinburgh|glasgow|wales|"
                     r"manchester|birmingham, uk|krakow|kraków|wroclaw|wrocław|gdansk|gdańsk|poznan|poznań|"
                     r"bucharest|cluj|porto|valencia|munich|hamburg|frankfurt|cologne|rotterdam|eindhoven|"
                     r"bangalore|chennai|noida|gurgaon|gurugram|mumbai|delhi|pakistan|karachi|lahore|egypt|cairo|"
                     r"nigeria|lagos|kenya|nairobi|south africa|cape town|johannesburg|turkey|istanbul|"
                     r"vietnam|thailand|malaysia|indonesia|new zealand|chile|peru|uruguay|eu|eea|cet|cest)\b", re.I)

# Remote roles that are really for Europe / elsewhere, judged from the description or the
# employer's legal form (only used when the location itself just says "Remote").
_NON_US_REMOTE_DESC = re.compile(
    r"\b(remote|work(ing)?|based|located|reside|residing|live|living)\s+(\w+\s+){0,3}"
    r"(in|within|from|across)\s+(the\s+)?(eu|e\.u\.|europe|emea|eea|uk|united kingdom|poland|germany|spain|"
    r"portugal|greece|romania|netherlands|ireland|france|italy|czech\w*|hungary|bulgaria|serbia|ukraine|"
    r"latam|latin america|india|canada|apac)\b|"
    r"\b(european|cet|cest|eet|uk|gmt|bst)\s+(time ?zones?|working hours|business hours|hours)\b|"
    r"\b(right|eligib\w+|authori[sz]ed|permit)\s+to\s+work\s+in\s+(the\s+)?(eu|uk|united kingdom|europe)\b|"
    r"\b(eu|uk) work permit\b|\bb2b contract\b|\bcontrato\b|\bumowa\b|"
    r"(€|£|\bEUR\b|\bGBP\b|\bPLN\b|\bRON\b|\bCZK\b|\bINR\b)\s?\d", re.I)
_NON_US_COMPANY = re.compile(r"\b(gmbh|polska|sp\.? ?z ?o\.? ?o\.?|s\.?a\.?r\.?l|s\.?r\.?l|b\.v\.|a/s|oy|ab|ag|"
                             r"s\.?p\.?a|kft|d\.o\.o|ltda|pvt\.? ltd|private limited)\b", re.I)


def location_reason(job: dict, search: dict) -> str | None:
    """search.allowed_locations (e.g. [Austin, Round Rock, ...]): keep jobs located there,
    or remote (in the US when search.remote_us_only, default true). None = keep."""
    allowed = [a for a in (search.get("allowed_locations") or []) if a]
    if not allowed:
        return None
    loc = (job.get("location") or "").strip()
    desc = job.get("description") or ""
    if loc and re.search(r"\b(" + "|".join(re.escape(a) for a in allowed) + r")\b", loc, re.I):
        return None
    mode = detect_work_mode(job.get("title", "") or "", loc, desc)
    if mode == "remote":
        if search.get("remote_us_only", True):
            if _NON_US.search(loc):
                return f"remote outside the US ({loc[:40]})"
            if not re.search(r"\b(us|u\.s\.?|usa|united states|america)\b", loc, re.I):   # just "Remote"
                m = _NON_US_REMOTE_DESC.search(desc[:8000])
                if m:
                    return f"remote outside the US ('{m.group(0).strip()[:40]}')"
                m = _NON_US_COMPANY.search(job.get("company") or "")
                if m:
                    return f"remote outside the US (company {job.get('company')})"
        return None
    if not loc or _GENERIC_LOC.match(loc):
        return None                       # can't tell where — let scoring / you decide
    return f"location {loc[:40]} (want {', '.join(allowed[:3])}{'…' if len(allowed) > 3 else ''} or remote)"


def wanted_modes(search: dict) -> set[str]:
    """search.work_mode: any | remote | hybrid | onsite | [remote, hybrid]; remote_only: true."""
    if search.get("remote_only"):
        return {"remote"}
    wm = search.get("work_mode", "any")
    modes = {wm} if isinstance(wm, str) else set(wm or [])
    modes = {m.lower().replace("-", "").replace("on site", "onsite") for m in modes}
    return set() if not modes or "any" in modes else modes & set(WORK_MODES)


# Staffing / recruiting agencies (post the same opening many times, for a hidden client).
STAFFING_FIRMS = [
    "insight global", "cynet systems", "tekwissen", "robert half", "randstad", "adecco", "kforce",
    "teksystems", "apex systems", "aerotek", "kelly services", "manpower", "experis", "hays",
    "collabera", "motion recruitment", "jobot", "cybercoders", "akkodis", "modis", "beacon hill",
    "judge group", "mondo", "signature consultants", "brooksource", "huxley", "harnham",
    "bright vision technologies", "noblesoft", "spar information systems", "dcm infotech", "itmates",
    "onwardpath", "diverse lynx", "mindlance", "pyramid consulting", "ampcus", "avance consulting",
    "itech us", "vaco", "addison group", "russell tobin", "yoh", "volt", "lancesoft", "compunnel",
    "rangam", "talentify", "system one", "nesco resource", "allegis", "hirequest", "staffmark",
    "express employment", "spherion", "actalent", "astreya", "softpath", "tanisha systems",
    "genesis10", "the dignify solutions", "sohoit", "intellectt", "radiant digital", "ust global staffing",
]
_STAFFING_NAME = re.compile(r"\b(staffing|recruit(ing|ers?|ment)|talent (partners|solutions|group)|"
                            r"search (group|partners)|placement|manpower|workforce solutions)\b", re.I)
_AGENCY_TEXT = re.compile(r"\b(our client|on behalf of (a|our) client|for (a|one of our) clients?|"
                          r"client of ours|c2c|corp[- ]to[- ]corp|w-?2 (only|contract)|1099)\b", re.I)


# Postings that say "W2" are nearly always contracts through an agency (you're on the agency's
# payroll, often hourly, no company benefits). Not matched: "W-2 forms" etc. in payroll/tax roles.
_W2 = re.compile(r"\bW-?2\b(?!\s*(forms?|statements?|reporting|processing|preparation|filings?|reconciliation))", re.I)


_CLEARANCE = re.compile(
    r"\b(?:(?:active|current|valid|existing|interim|final|dod|doe|government|federal|u\.?s\.?|us)\s+)?"
    r"(?:security\s+clearances?|(?:top\s+)?secret(?:\s*/\s*sci)?\s+clearances?|clearances?\s+(?:is\s+|are\s+)?required|"
    r"(?:active|current)\s+clearances?|(?:ts|t\.s\.)\s*/\s*sci|top[\s-]+secret|\bsci\s+eligib\w*|"
    r"(?:full[\s-]+scope|ci|counter[\s-]?intelligence|lifestyle)\s+poly(?:graph)?|polygraph|"
    r"public[\s-]+trust(?:\s+clearance)?|(?:dod|doe)\s+(?:q|l)\s+clearance|\bq\s+clearance|"
    r"(?:clearable|able\s+to\s+obtain\s+(?:and\s+maintain\s+)?(?:a\s+)?(?:\w+\s+)?clearance)|"
    r"(?:eligib\w+\s+(?:for|to\s+obtain)\s+(?:a\s+)?(?:\w+\s+)?(?:security\s+)?clearance))", re.I)
_CLEARANCE_NEG = re.compile(r"\b(?:no|not|without|doesn'?t|does\s+not)\s+(?:\w+\s+){0,3}$", re.I)


def clearance_reason(job: dict, filters: dict) -> str | None:
    """filters.exclude_clearance (default true): drop any job that mentions needing a security
    clearance (Secret, TS/SCI, Top Secret, polygraph, Public Trust, 'clearable'…) in its
    title or description. 'No clearance required' is not a match."""
    if filters.get("exclude_clearance", True) is False:
        return None
    text = f"{job.get('title') or ''}\n{job.get('description') or ''}"
    for m in _CLEARANCE.finditer(text):
        before = text[max(0, m.start() - 40):m.start()]
        after = text[m.end():m.end() + 25]
        if _CLEARANCE_NEG.search(before) or re.match(r"\s*(?:is\s+)?not\s+(?:required|needed)", after, re.I):
            continue
        return f"security clearance ('{m.group(0).strip()[:40]}')"
    return None


def staffing_reason(job: dict, filters: dict) -> str | None:
    """filters.exclude_staffing: drop staffing-agency postings (known firms, agency-style
    names, 'our client' / C2C / 1099 wording, or any "W2" mention — filters.exclude_w2)."""
    if not filters.get("exclude_staffing"):
        return None
    comp = (job.get("company") or "").lower()
    firms = STAFFING_FIRMS + [str(x).lower() for x in filters.get("staffing_firms_extra") or []]
    if any(f and re.search(r"\b" + re.escape(f) + r"\b", comp) for f in firms) or _STAFFING_NAME.search(comp):
        return f"staffing agency ({job.get('company')})"
    sal = (job.get("salary") or "").lower()
    nums = [float(x) for x in re.findall(r"\$\s*(\d+(?:\.\d+)?)(?!\s*k)", sal.replace(",", ""))]
    if nums and max(nums) < 300 and "year" not in sal and "k" not in sal:
        return f"hourly pay ({job.get('salary')}) — usually a contract/agency role"
    m = _AGENCY_TEXT.search(f"{job.get('title') or ''} {(job.get('description') or '')[:5000]}")
    if m:
        return f"agency / contract posting ('{m.group(0)}')"
    if filters.get("exclude_w2", True):
        m = _W2.search(f"{job.get('title') or ''}\n{(job.get('description') or '')[:8000]}")
        if m:
            return f"W2 contract role ('{m.group(0).strip()}')"
    return None


def filter_reason(job: dict, filters: dict, tracks: list | None = None,
                  search: dict | None = None) -> str | None:
    """Return a human-readable reason to drop the job, or None to keep it."""
    title = job.get("title", "") or ""
    company = job.get("company", "") or ""
    desc = job.get("description", "") or ""

    rx = _words_re(filters.get("title_exclude", []))
    if rx and (m := rx.search(title)):
        return f"title excludes '{m.group(1)}'"

    why = staffing_reason(job, filters) or clearance_reason(job, filters)
    if why:
        return why

    blocked = {c.lower().strip() for c in filters.get("company_blocklist", [])}
    if company.lower().strip() in blocked:
        return f"company blocklisted ({company})"

    for pat in filters.get("description_exclude", []) or []:
        if desc and re.search(pat, desc, re.I):
            return f"description matches /{pat}/"

    floor = float(filters.get("min_salary") or 0)
    if floor:
        top = parse_salary_max(job.get("salary", ""))
        if top is not None and top < floor:
            return f"salary max {top:,.0f} < {floor:,.0f}"

    if search:
        days = float(search.get("posted_within_days") or 0)
        if days:
            age = parse_posted_days(job.get("posted"), job.get("found_at", ""))
            if age is not None and age > days:
                return f"posted {age:.0f} days ago (> {days:.0f})"
        why = location_reason(job, search)
        if why:
            return why
        modes = wanted_modes(search)
        if modes:
            mode = detect_work_mode(title, job.get("location", "") or "", desc)
            if mode not in modes and not (mode == "unknown" and not desc):
                return f"work mode {mode} (want {'/'.join(sorted(modes))})"

    if tracks:
        title_l = title.lower()
        if not any(any(t in title_l for t in tr.titles) for tr in tracks if tr.titles):
            return "title doesn't match any track"
    return None
