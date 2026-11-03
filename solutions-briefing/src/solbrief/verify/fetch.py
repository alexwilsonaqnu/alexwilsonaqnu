"""Fetch a cited source and extract its readable text (HTML via trafilatura, PDF via pypdf).

Results are cached in `source_fetch_cache` so the drafter and verifier see the same text and repeated runs
don't re-hit sources. Re-verification passes `max_age=timedelta(0)` to force a fresh fetch.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from solbrief.adapters.base import PoliteClient, SourceError
from solbrief.db import session_scope
from solbrief.db.models import SourceFetch
from solbrief.verify.sources import is_public_url

log = logging.getLogger(__name__)
DEFAULT_MAX_AGE = timedelta(days=7)
MAX_BYTES = 15_000_000


@dataclass(frozen=True)
class Document:
    url: str
    status: int  # HTTP status; 0 = not fetched (blocked/unresolvable/transport failure)
    text: str
    sha256: str | None
    fetched_at: datetime

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.text)


Fetcher = Callable[[str], Document]


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PUBMED_ARTICLE = re.compile(r"^https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d+)/?(?:[?#].*)?$")
EUTILS_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def retrieval_url(url: str) -> str:
    """Where to actually fetch a cited URL. PubMed article pages are read through NCBI's E-utilities API, which
    is NCBI's sanctioned route for automated access (the website blocks bots); the citation stays the PubMed URL."""
    m = _PUBMED_ARTICLE.match(url)
    if m:
        return f"{EUTILS_EFETCH}?db=pubmed&id={m.group(1)}&rettype=abstract&retmode=text&tool=solbrief"
    return url


def scrub(text: str) -> str:
    """Remove email addresses from source text before caching: we never store personal contact details, and an
    email can then never end up inside a verified quote."""
    return _EMAIL.sub("[email removed]", text)


def extract_text(content: bytes, content_type: str) -> str:
    return scrub(_extract(content, content_type))


def _extract(content: bytes, content_type: str) -> str:
    ctype = content_type.lower()
    if "pdf" in ctype or content[:5] == b"%PDF-":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if "html" in ctype or b"<html" in content[:2000].lower():
        import trafilatura

        html = content.decode("utf-8", errors="replace")
        text = trafilatura.extract(html, include_tables=True, include_comments=False, favor_recall=True)
        if text:
            return text
        return re.sub(r"<[^>]+>", " ", html)  # last resort: tag-stripped
    return content.decode("utf-8", errors="replace")


def http_fetcher(client: PoliteClient | None = None, max_age: timedelta = DEFAULT_MAX_AGE) -> Fetcher:
    client = client or PoliteClient()

    def fetch(url: str) -> Document:
        now = datetime.now(UTC)
        with session_scope() as s:
            cached = s.scalar(select(SourceFetch).where(SourceFetch.url == url))
            if cached and cached.http_status == 200 and now - cached.fetched_at <= max_age and cached.text:
                return Document(url, 200, cached.text, cached.content_sha256, cached.fetched_at)
        if not is_public_url(url):
            return Document(url, 0, "", None, now)
        try:
            resp = client.get(retrieval_url(url), raise_for_status=False)
        except SourceError as exc:
            log.warning("fetch failed %s: %s", url, exc)
            return Document(url, 0, "", None, now)
        text, sha = "", None
        if resp.status_code == 200:
            body = resp.content[:MAX_BYTES]
            sha = hashlib.sha256(body).hexdigest()
            try:
                text = extract_text(body, resp.headers.get("content-type", ""))
            except Exception:  # malformed PDFs etc. → treated as unreadable, never as verified
                log.exception("text extraction failed for %s", url)
        doc = Document(url, resp.status_code, text, sha, now)
        with session_scope() as s:
            stmt = insert(SourceFetch).values(
                url=url,
                fetched_at=now,
                http_status=resp.status_code,
                content_sha256=sha,
                text=text or None,
                etag=resp.headers.get("etag"),
            )
            s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["url"],
                    set_={
                        "fetched_at": stmt.excluded.fetched_at,
                        "http_status": stmt.excluded.http_status,
                        "content_sha256": stmt.excluded.content_sha256,
                        "text": stmt.excluded.text,
                        "etag": stmt.excluded.etag,
                    },
                )
            )
        return doc

    return fetch
