"""Stage-1 retrieval: model number -> candidate service documents.

Hits the public ServiceMatters search; falls back to the local doc registry written by
ingestion whenever the network call fails, and says so in the result.
"""

from __future__ import annotations

import json
from typing import Any

from src.config import DOC_REGISTRY_PATH, HTTP_TIMEOUT_SECONDS, service_matters_url


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
                    "models": doc.get("models", []),
                    "pages": doc.get("pages"),
                    "figure_count": doc.get("figure_count"),
                }
            )
    return matches


def service_matters_search(model_number: str) -> dict[str, Any]:
    """Return candidate doc_ids for a model number, for stage-2 `manual_search`."""
    source = "servicematters"
    fallback_reason: str | None = None
    remote: list[dict[str, Any]] = []
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
        raw = payload.get("results", payload) if isinstance(payload, dict) else payload
        for item in raw if isinstance(raw, list) else []:
            remote.append(
                {
                    "doc_id": item.get("doc_id") or item.get("id"),
                    "doc_title": item.get("title"),
                    "models": item.get("models", [model_number]),
                    "url": item.get("url"),
                }
            )
    except Exception as exc:  # offline, 4xx/5xx, or non-JSON body
        fallback_reason = f"{type(exc).__name__}: {exc}"

    if remote:
        results = remote
    else:
        results = _local_lookup(model_number)
        source = "local_doc_registry"
        if fallback_reason is None:
            fallback_reason = "ServiceMatters returned no results"

    return {
        "model_number": model_number,
        "source": source,
        "fell_back": source != "servicematters",
        "fallback_reason": fallback_reason,
        "doc_ids": [r["doc_id"] for r in results if r.get("doc_id")],
        "results": results,
    }
