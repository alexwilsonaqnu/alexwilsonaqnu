"""Expert affiliation check: the institutional contact page must exist and name the person and institution.

Contact paths are institutional pages or media-relations offices only (also enforced by a DB CHECK).
An expert who previously verified but no longer appears on their page is marked `moved`.
"""

from __future__ import annotations

from dataclasses import dataclass

from solbrief.verify.fetch import Fetcher
from solbrief.verify.spans import normalize

_GENERIC_INSTITUTION_WORDS = frozenset({
    "university", "college", "school", "of", "the", "and", "for", "at", "department", "dept", "center", "centre",
    "institute", "institution", "program", "office", "state", "public", "health", "inc", "llc", "foundation",
})  # fmt: skip


@dataclass(frozen=True)
class ExpertResult:
    ok: bool
    reason: str | None = None


def check_expert(name: str, institution: str | None, contact_path_url: str, fetch: Fetcher) -> ExpertResult:
    url = contact_path_url.strip()
    if not url.lower().startswith("https://") or "@" in url or url.lower().startswith(("mailto:", "tel:")):
        return ExpertResult(False, "contact_not_institutional_url")
    doc = fetch(url)
    if not doc.ok:
        return ExpertResult(False, "contact_page_unreachable")
    page = normalize(doc.text)
    tokens = [t for t in normalize(name).replace(".", " ").split() if t]
    if len(tokens) < 2:
        return ExpertResult(False, "name_incomplete")
    first, last = tokens[0], tokens[-1]
    if last not in page or not (first in page or f"{first[0]}." in page or f" {first[0]} " in page):
        return ExpertResult(False, "name_not_on_contact_page")
    if institution:
        distinctive = [
            w for w in normalize(institution).replace(",", " ").split() if w not in _GENERIC_INSTITUTION_WORDS
        ]
        if distinctive and not any(w in page for w in distinctive):
            return ExpertResult(False, "institution_not_on_contact_page")
    return ExpertResult(True)
