"""Stage-1 retrieval: model number -> candidate service documents.

Written against the live ServiceMatters search endpoint. Three things about that API
drive the shape of this module:

1. **Their relevance score is unusable.** Every hit for a model-number query comes back
   at ~4e-05 — the query matches an exact model field, so nothing differentiates. We
   cannot sort by `_score`; we rank by document category instead, because for a
   diagnostic call a Tech Sheet is worth more than a Warranty page no matter what an
   inverted index thinks.

2. **There are two result types.** `wdl` entries carry a PDF attachment and need
   ingesting. `techline` entries ("Internal Note") carry their troubleshooting text
   *inline* in a `techNotes` field, plus an explicit `models` array — no PDF, no
   parsing, immediately usable.

3. **A model query returns the consumer shelf too** — owner's manual, energy guide,
   warranty, product registration. Useless on a repair call and actively harmful if it
   crowds out the Tech Sheet, so categories below the service line are dropped by default.

Falls back to the local doc registry when the network call fails, and says so.
"""

from __future__ import annotations

import json
from typing import Any

from src.config import DOC_REGISTRY_PATH, HTTP_TIMEOUT_SECONDS, service_matters_url

# Rank order for a diagnostic voice call. Lower is better. Anything absent from this map
# is treated as consumer-facing literature and excluded unless include_all is set.
CATEGORY_RANK: dict[str, int] = {
    "Tech Sheet": 10,               # fault codes, component tests, wiring — the core
    "Technical Manual": 20,         # full service procedures
    "Service Pointer": 30,          # known-failure bulletins, revision controlled
    "Internal Note": 40,            # techline field guidance, inline text
    "Job Aid": 45,
    "Parts List": 50,               # part numbers for the writeback
    "Parts Instruction Sheet": 60,
    "safety-and-installation-instructions": 70,
    "Installation Instructions": 80,
}

# Present in results, deliberately not retrieved for a repair call.
CONSUMER_CATEGORIES = {
    "Owners Manual", "Energy Guide", "Dimension Guide", "Warranty Page",
    "Product Registration", "Reference Sheet", "label",
}

DEFAULT_K = 6


def _first(fields: dict[str, Any], key: str) -> Any:
    value = fields.get(key)
    return value[0] if isinstance(value, list) and value else value


def _registry_docs() -> list[dict[str, Any]]:
    if not DOC_REGISTRY_PATH.exists():
        return []
    with DOC_REGISTRY_PATH.open(encoding="utf-8") as fh:
        return json.load(fh).get("docs", [])


def _local_lookup(model_number: str) -> list[dict[str, Any]]:
    needle = (model_number or "").strip().upper()
    matches: list[dict[str, Any]] = []
    for doc in _registry_docs():
        models = [m.upper() for m in doc.get("models", [])]
        # exact, then prefix — partial model numbers are common over a phone line
        if needle in models or any(m.startswith(needle) or needle.startswith(m) for m in models if m and needle):
            matches.append(
                {
                    "doc_id": doc["doc_id"],
                    "doc_title": doc.get("title"),
                    "category": doc.get("category", "ingested"),
                    "rank": 10,
                    "models": doc.get("models", []),
                    "ingested": True,
                }
            )
    return matches


def _parse_hit(hit: dict[str, Any], model_number: str) -> dict[str, Any] | None:
    fields = hit.get("fields") or {}
    category = _first(fields, "category") or ""
    kind = hit.get("_type")

    title = _first(fields, "title") or _first(fields, "header") or _first(fields, "subject")
    models = fields.get("models") or []
    # litPartNumber casing is inconsistent across records (W11684045 vs w11695259).
    # Normalize, or stage-2's doc_id filter silently drops half the candidates.
    raw_id = _first(fields, "litPartNumber") or hit.get("_id") or ""
    record: dict[str, Any] = {
        "doc_id": str(raw_id).upper(),
        "doc_title": title,
        "category": category or ("Internal Note" if kind == "techline" else "Unknown"),
        "kind": kind,
        "revision": _first(fields, "revision"),
        "modified_at": _first(fields, "modifiedAt"),
        "models": models,
    }

    if kind == "techline":
        # Inline troubleshooting text — no attachment, nothing to ingest.
        record["category"] = "Internal Note"
        record["text"] = _first(fields, "techNotes")
        record["doc_title"] = title or _summarize_note(record["text"])
        record["needs_ingestion"] = False
    else:
        record["url"] = _first(fields, "attachments.url")
        record["needs_ingestion"] = True

    record["rank"] = CATEGORY_RANK.get(record["category"], 999)
    # Only techline records state their own model coverage; for the rest this is unknown,
    # which is different from "does not cover this model".
    needle = (model_number or "").strip().upper()
    upper = [m.upper() for m in models if isinstance(m, str)]
    record["model_confirmed"] = (needle in upper) if upper else None
    return record


def _summarize_note(text: str | None) -> str:
    """techline notes have no title; their first 'Possible Concerns' line is the subject."""
    if not text:
        return "Internal note"
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("possible concerns"):
            return line.split(":", 1)[-1].strip()[:120] or "Internal note"
    return text.strip().splitlines()[0][:120]


def service_matters_search(
    model_number: str,
    k: int = DEFAULT_K,
    include_consumer_docs: bool = False,
) -> dict[str, Any]:
    """Return the service documents worth reading for this model, best first."""
    source = "servicematters"
    fallback_reason: str | None = None
    parsed: list[dict[str, Any]] = []
    total = None

    try:
        import httpx

        response = httpx.get(
            service_matters_url(),
            params={"q": model_number},
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        total = payload.get("total")
        for hit in payload.get("results") or []:
            record = _parse_hit(hit, model_number)
            if record and record.get("doc_id"):
                parsed.append(record)
    except Exception as exc:  # offline, 4xx/5xx, or non-JSON body
        fallback_reason = f"{type(exc).__name__}: {exc}"

    dropped: list[str] = []
    notes: list[dict[str, Any]] = []
    if parsed:
        kept = []
        for record in parsed:
            if not include_consumer_docs and (
                record["category"] in CONSUMER_CATEGORIES or record["rank"] == 999
            ):
                dropped.append(f"{record['category']}: {record['doc_title']}")
                continue
            # Notes carry their own text and cost nothing to use, so they are returned
            # alongside the documents rather than competing with them for the top-k.
            (notes if not record["needs_ingestion"] else kept).append(record)
        # Category rank first, then newest revision — their score cannot break ties.
        kept.sort(key=lambda r: (r["rank"], str(r.get("modified_at") or "")))
        results = kept[: max(1, int(k))]
    else:
        results = _local_lookup(model_number)
        source = "local_doc_registry"
        if fallback_reason is None:
            fallback_reason = "ServiceMatters returned no usable results"

    # Stage 2 resolves a doc_id to a URL through this catalog, which is what lets it
    # fetch a document nobody ingested ahead of time.
    from src.tools.doc_cache import remember

    remember(results)

    return {
        "model_number": model_number,
        "source": source,
        "fell_back": source != "servicematters",
        "fallback_reason": fallback_reason,
        "total_available": total,
        "excluded_consumer_docs": len(dropped),
        # doc_ids feed stage 2's filter.
        "doc_ids": [r["doc_id"] for r in results if r.get("doc_id")],
        "results": results,
        # Inline field guidance, immediately readable — no PDF, no ingestion.
        "notes": notes,
    }
