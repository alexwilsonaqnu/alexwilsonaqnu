"""On-the-fly document acquisition: a ServiceMatters hit becomes a searchable document.

Without this, stage-1 routing is theatre. `service_matters_search` names the Tech Sheet,
the Technical Manual and four Service Pointers for a model, and `manual_search` can read
exactly the documents someone happened to ingest by hand beforehand — so the agent routes
confidently to a document it cannot open. Here the routing is real: a doc_id the corpus
has never seen is fetched and indexed at the moment it is asked for.

Measured on the WTW5057LW0 document set, which is what the budget below is built around:

    Tech Sheet         0.25 MB     2 pages   0.6s download   0.3s ingest
    Service Pointer    0.53 MB    12 pages   0.9s download   1.0s ingest
    Technical Manual   21.1 MB   156 pages   1.7s download    86s ingest

Downloads are uniformly cheap; ingestion is what costs, and it scales with pages. The
diagnostic documents a technician actually needs mid-call — Tech Sheet, Service Pointers —
land in about a second and are fetched inline. A 156-page Technical Manual cannot be made
to happen inside a phone call, so it is indexed on a background thread and is available
for the rest of the session instead of blocking the turn it was asked for.

Seam: in production this whole module is a pre-built index. Documents are ingested once by
a pipeline (Document AI -> vector store), not fetched per call, and the size split stops
mattering. The tool contract does not change.
"""

from __future__ import annotations

import re
import threading
from typing import Any

from src.config import DATA_DIR, DOC_REGISTRY_PATH, HTTP_TIMEOUT_SECONDS
from src.telemetry import span

# Ingest inline up to this many pages; hand anything bigger to a background thread.
# 16 pages is ~1.5s on a Service Pointer and ~9s worst case on dense manual pages —
# the ceiling on what a technician will wait mid-sentence.
SYNC_PAGE_BUDGET = 16
MAX_PDF_BYTES = 48 * 1024 * 1024
DOWNLOAD_TIMEOUT = max(30.0, HTTP_TIMEOUT_SECONDS)

_lock = threading.Lock()
_catalog: dict[str, dict[str, Any]] = {}   # ServiceMatters doc_id -> record
_indexing: dict[str, threading.Thread] = {}  # doc_id -> background ingest in flight


def remember(records: list[dict[str, Any]]) -> None:
    """Record what stage 1 saw, so stage 2 can resolve a doc_id to a URL."""
    with _lock:
        for record in records:
            doc_id = (record.get("doc_id") or "").upper()
            if doc_id and record.get("url"):
                _catalog[doc_id] = record


def _registry_ids() -> list[str]:
    if not DOC_REGISTRY_PATH.exists():
        return []
    import json

    with DOC_REGISTRY_PATH.open(encoding="utf-8") as fh:
        return [d.get("doc_id", "") for d in json.load(fh).get("docs", [])]


def local_doc_id(doc_id: str) -> str | None:
    """Map a ServiceMatters lit part number to an ingested doc, if we already have it.

    Their ids are part numbers (W11428598); ours are file slugs
    (w11428598_tech_sheet), so this is a containment match, not equality.
    """
    needle = (doc_id or "").strip().lower()
    if not needle:
        return None
    # Intersected with what is actually searchable: a registry entry whose chunks were
    # lost to an interrupted bulk write would otherwise mark the document "cached"
    # forever and it would never be re-indexed.
    from src.ingest.ingest_pdf import indexed_doc_ids

    searchable = indexed_doc_ids()
    for local in _registry_ids():
        if local in searchable and (needle == local.lower() or needle in local.lower()):
            return local
    return None


def _ingest(pdf_path, models: list[str], title: str | None) -> dict[str, Any]:
    from src.ingest.ingest_pdf import ingest

    return ingest(pdf_path, [m.upper() for m in models], title=title)


def _background_ingest(pdf_path, models: list[str], title: str | None, key: str) -> None:
    try:
        with span("ingest.background", doc=key) as s:
            summary = _ingest(pdf_path, models, title)
            s["chunks"] = summary["chunks"]
            s["pages"] = summary["pages"]
    except Exception:
        pass  # a failed background index must never surface as a broken call
    finally:
        with _lock:
            _indexing.pop(key, None)


def ensure_local(doc_id: str, models: list[str]) -> tuple[str | None, str]:
    """Make `doc_id` searchable. Returns (local doc_id or None, status).

    status is one of: cached | fetched | indexing | no_url | too_large | failed | unknown
    """
    doc_id = (doc_id or "").strip()
    if not doc_id:
        return None, "unknown"

    existing = local_doc_id(doc_id)
    if existing:
        return existing, "cached"

    key = doc_id.upper()
    with _lock:
        record = _catalog.get(key)
        if key in _indexing:
            return None, "indexing"
    if not record:
        return None, "unknown"
    url = record.get("url")
    if not url:
        return None, "no_url"

    with span("ingest.fetch", doc=key, url=url) as s:
        try:
            import httpx

            response = httpx.get(url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
            response.raise_for_status()
            body = response.content
        except Exception as exc:
            s["error"] = f"{type(exc).__name__}: {exc}"
            return None, "failed"

        if len(body) > MAX_PDF_BYTES:
            s["bytes"] = len(body)
            return None, "too_large"

        slug = re.sub(r"[^a-z0-9]+", "_", (record.get("category") or "doc").lower()).strip("_")
        pdf_path = DATA_DIR / f"{key.lower()}_{slug}.pdf"
        try:
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(body)
            import pdfplumber

            with pdfplumber.open(str(pdf_path)) as pdf:
                pages = len(pdf.pages)
        except Exception as exc:
            s["error"] = f"{type(exc).__name__}: {exc}"
            return None, "failed"
        s["pages"] = pages

        title = record.get("doc_title")
        if pages <= SYNC_PAGE_BUDGET:
            try:
                summary = _ingest(pdf_path, models, title)
            except Exception as exc:
                s["error"] = f"{type(exc).__name__}: {exc}"
                return None, "failed"
            s["chunks"] = summary["chunks"]
            return summary["doc_id"], "fetched"

        # Too big to index inside a turn. Start it and let the caller answer from what it
        # already has; the document is searchable for the rest of the session.
        thread = threading.Thread(
            target=_background_ingest, args=(pdf_path, models, title, key), daemon=True
        )
        with _lock:
            _indexing[key] = thread
        thread.start()
        return None, "indexing"


def reset() -> None:
    """Test hook: forget the stage-1 catalog. Does not touch ingested documents."""
    with _lock:
        _catalog.clear()
