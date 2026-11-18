"""URL canonicalization + cross-site job fingerprints for dedup."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

_TRACKING = re.compile(r"^(utm_|gh_src|ref|refid|trackingid|position|pagenum|src|source|from|"
                       r"vjs|tk|advn|adid|ad|sjdu|acatk|pub|camk|xkcb|xpse|xfps|jsa|guid|cs|cb)", re.I)


def canonical_url(url: str) -> str:
    """Stable identity for a job URL: one job → one string, regardless of tracking params."""
    if not url:
        return ""
    u = urlparse(url.strip())
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    qs = parse_qs(u.query)

    if "indeed." in host:
        jk = (qs.get("jk") or qs.get("vjk") or [""])[0]
        if jk:
            return f"https://www.indeed.com/viewjob?jk={jk}"
    if "glassdoor." in host:
        jl = (qs.get("jl") or qs.get("jobListingId") or [""])[0]
        if not jl:
            m = re.search(r"jobListingId=(\d+)", url) or re.search(r"(\d{9,})", u.path)
            jl = m.group(1) if m else ""
        if jl:
            return f"https://www.glassdoor.com/job-listing/?jl={jl}"
    if "linkedin.com" in host:
        m = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)", u.path) or None
        jid = m.group(1) if m else (qs.get("currentJobId") or [""])[0]
        if jid:
            return f"https://www.linkedin.com/jobs/view/{jid}"

    kept = {k: v for k, v in qs.items() if not _TRACKING.match(k)}
    path = re.sub(r"/+$", "", u.path) or "/"
    return urlunparse(("https", host, path, "", urlencode(kept, doseq=True), ""))


def external_id(url: str) -> str:
    c = canonical_url(url)
    m = re.search(r"(?:jk|jl)=([\w-]+)$", c) or re.search(r"/jobs/view/(\d+)$", c)
    return m.group(1) if m else ""


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|the)\b\.?", " ", s)
    s = re.sub(r"\(.*?\)|[-–—|,].*remote.*$", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def fingerprint(company: str, title: str, location: str = "") -> str:
    """Same job posted on Indeed and Glassdoor → same fingerprint."""
    city = re.split(r"[,(]", location or "")[0]
    key = f"{_norm(company)}|{_norm(title)}|{_norm(city)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def role_key(company: str, title: str) -> str:
    """Same opening reposted under different cities/sites → same key.
    'Cognizant' + 'Senior Agentic AI Engineer (Remote)' == 'Senior Agentic AI Engineer - Remote'."""
    t = (title or "").lower()
    t = re.sub(r"\b(remote|hybrid|on-?site|in[- ]office|us|usa|united states|work from home|wfh)\b", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return f"{_norm(company)}|{t}"


_CO_SUFFIX = re.compile(r"\b(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|the|plc|llp|lp|gmbh|ag|sa|"
                        r"holdings?|group|n\.?a)\b\.?", re.I)


def company_words(company: str) -> list[str]:
    """'AIG PC Global Services, Inc.' → ['aig', 'pc', 'global', 'services']."""
    s = re.sub(r"\(.*?\)", " ", (company or "").lower())
    s = _CO_SUFFIX.sub(" ", s)
    return re.findall(r"[a-z0-9]+", s)


def same_company(a: str, b: str) -> bool:
    """Same employer under name variants: 'AIG' ~ 'AIG PC Global Services, Inc.',
    'Intercontinental Exchange' ~ 'Intercontinental Exchange Holdings, Inc.' (whole-word prefix)."""
    wa, wb = company_words(a), company_words(b)
    if not wa or not wb:
        return False
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return long_[:len(short)] == short


def title_key(title: str) -> str:
    """Title part of role_key: location words and punctuation dropped."""
    return role_key("", title).split("|", 1)[1]

