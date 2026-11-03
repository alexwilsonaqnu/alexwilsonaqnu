"""Source identity, policy and evidence-grade caps.

* Publisher identity (for the "two independent sources" rule) is the registrable domain, with known
  agencies/publishers folded together (e.g. thecommunityguide.cdc.gov and cdc.gov are both CDC).
* Blocked sources: terms forbid automated use (docs/PLAN.md §1.2) → claims citing them never verify.
* Grade caps: the strongest grade a source can justify. A drafter-declared source type is only honoured
  when the domain is plausible for it and the fetched document actually describes that kind of study.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from functools import cache
from urllib.parse import urlsplit

import tldextract

from solbrief.db.models import EvidenceGrade

GRADE_ORDER = [
    EvidenceGrade.anecdotal,
    EvidenceGrade.self_reported,
    EvidenceGrade.observational_or_admin_data,
    EvidenceGrade.quasi_experimental,
    EvidenceGrade.meta_analysis_or_rct,
]

SOURCE_TYPE_CAP: dict[str, EvidenceGrade] = {
    "systematic_review": EvidenceGrade.meta_analysis_or_rct,
    "rct": EvidenceGrade.meta_analysis_or_rct,
    "quasi_experimental": EvidenceGrade.quasi_experimental,
    "government_report": EvidenceGrade.observational_or_admin_data,
    "admin_data": EvidenceGrade.observational_or_admin_data,
    "peer_reviewed_observational": EvidenceGrade.observational_or_admin_data,
    "org_report": EvidenceGrade.self_reported,
    "news": EvidenceGrade.anecdotal,
    "other": EvidenceGrade.anecdotal,
}
SOURCE_TYPES = tuple(SOURCE_TYPE_CAP)

# Research-grade claims (systematic review / RCT / QED) are only accepted from these registrable domains
# (government, universities, journals, evidence clearinghouses, research orgs).
RESEARCH_DOMAINS = frozenset({
    "doi.org", "nih.gov", "cdc.gov", "ed.gov", "hhs.gov", "ahrq.gov", "acf.hhs.gov", "hud.gov", "dol.gov",
    "usda.gov", "epa.gov", "census.gov", "bls.gov", "nber.org", "povertyactionlab.org", "mdrc.org",
    "mathematica.org", "urban.org", "rand.org", "brookings.edu", "campbellcollaboration.org", "wiley.com",
    "sagepub.com", "springer.com", "sciencedirect.com", "elsevier.com", "tandfonline.com", "jamanetwork.com",
    "nejm.org", "thelancet.com", "bmj.com", "plos.org", "nature.com", "science.org", "aeaweb.org",
    "oup.com", "cambridge.org", "frontiersin.org", "mdpi.com", "biomedcentral.com", "healthaffairs.org",
    "ajph.aphapublications.org", "aphapublications.org", "annualreviews.org", "jstor.org", "ssrn.com",
    "arxiv.org", "osf.io", "europepmc.org", "who.int", "oecd.org", "worldbank.org", "un.org",
})  # fmt: skip

NEWS_DOMAINS = frozenset({
    "nytimes.com", "washingtonpost.com", "wsj.com", "usatoday.com", "apnews.com", "reuters.com", "npr.org",
    "cnn.com", "foxnews.com", "nbcnews.com", "cbsnews.com", "abcnews.go.com", "go.com", "politico.com",
    "axios.com", "theguardian.com", "bloomberg.com", "latimes.com", "chicagotribune.com", "propublica.org",
    "vox.com", "theatlantic.com", "statnews.com", "kffhealthnews.org", "pewresearch.org", "medium.com",
    "substack.com",
})  # fmt: skip

STUDY_TERMS = {
    "systematic_review": re.compile(r"systematic review|meta-analys|studies|task force|clearinghouse", re.I),
    "rct": re.compile(r"randomi[sz]ed|controlled trial|\brct\b", re.I),
    "quasi_experimental": re.compile(
        r"quasi-experimental|difference-in-differences|regression discontinuity"
        r"|matched comparison|comparison group|interrupted time series",
        re.I,
    ),
}

# Terms of use forbid automated ingestion (docs/PLAN.md §1.2). Never verify claims citing these.
BLOCKED_DOMAINS = frozenset({"cochranelibrary.com", "cochrane.org", "solutionsjournalism.org"})

# Folded publisher identities (registrable domain → publisher)
PUBLISHER_ALIASES = {
    "cdc.gov": "U.S. Centers for Disease Control and Prevention",
    "ed.gov": "U.S. Department of Education",
    "nih.gov": "U.S. National Institutes of Health",
    "doi.org": None,  # resolved per-DOI prefix below
}

_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)  # bundled snapshot; no network


def registrable_domain(url: str) -> str:
    parts = _extract(url)
    return ".".join(p for p in (parts.domain, parts.suffix) if p).lower()


def publisher_id(url: str) -> str:
    """Identity used for independence. DOIs are grouped by registrant prefix (10.xxxx = one publisher)."""
    domain = registrable_domain(url)
    if domain == "doi.org":
        path = urlsplit(url).path.lstrip("/")
        return "doi:" + path.split("/", 1)[0] if path else "doi.org"
    return PUBLISHER_ALIASES.get(domain) or domain


def is_blocked(url: str) -> bool:
    return registrable_domain(url) in BLOCKED_DOMAINS


def effective_cap(source_type: str | None, url: str, document_text: str) -> EvidenceGrade:
    """Grade cap for one verified source, after plausibility checks on the declared type."""
    declared = source_type if source_type in SOURCE_TYPE_CAP else "other"
    domain = registrable_domain(url)
    if domain in NEWS_DOMAINS:
        return EvidenceGrade.anecdotal
    if declared in STUDY_TERMS:
        research_host = domain in RESEARCH_DOMAINS or domain.endswith((".gov", ".edu"))
        if not research_host or not STUDY_TERMS[declared].search(document_text):
            declared = "org_report" if not research_host else "government_report"
    return SOURCE_TYPE_CAP[declared]


def strongest(grades: list[EvidenceGrade]) -> EvidenceGrade | None:
    return max(grades, key=GRADE_ORDER.index) if grades else None


def weaker(a: EvidenceGrade, b: EvidenceGrade) -> EvidenceGrade:
    return min(a, b, key=GRADE_ORDER.index)


@cache
def _resolves_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def is_public_url(url: str) -> bool:
    """Only fetch public http(s) URLs (drafts are model-generated; never let them reach internal hosts)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    return _resolves_public(parts.hostname)
