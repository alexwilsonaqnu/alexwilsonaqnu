"""Stage-2 retrieval: BM25 over ingested page chunks.

Seam: production replaces the BM25 index with a Vertex vector index (or hybrid dense +
sparse). The tool contract — query, doc_ids, k, and the result field set — stays put so
the orchestrator, the evaluator and the evals never learn the difference.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from src.config import CHUNKS_PATH

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_lock = threading.Lock()
_cache: dict[str, Any] = {"mtime": None, "chunks": [], "bm25": None}


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def load_chunks() -> list[dict[str, Any]]:
    if not CHUNKS_PATH.exists():
        return []
    chunks: list[dict[str, Any]] = []
    with CHUNKS_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def _index() -> tuple[list[dict[str, Any]], Any]:
    """Build (and memoize) the BM25 index, invalidating when chunks.jsonl changes."""
    mtime = CHUNKS_PATH.stat().st_mtime if CHUNKS_PATH.exists() else None
    with _lock:
        if _cache["mtime"] == mtime and _cache["bm25"] is not None:
            return _cache["chunks"], _cache["bm25"]
        chunks = load_chunks()
        bm25 = None
        if chunks:
            from rank_bm25 import BM25Okapi

            bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])
        _cache.update({"mtime": mtime, "chunks": chunks, "bm25": bm25})
        return chunks, bm25


def manual_search(query: str, doc_ids: list[str] | None = None, k: int = 5) -> dict[str, Any]:
    """Search ingested manual pages, optionally restricted to stage-1 candidate docs."""
    chunks, bm25 = _index()
    if not chunks or bm25 is None:
        return {
            "query": query,
            "results": [],
            "note": "No manual has been ingested yet. Run src.ingest.ingest_pdf first.",
        }

    scores = bm25.get_scores(tokenize(query))
    wanted = {d for d in (doc_ids or []) if d}
    scored = [
        (float(score), chunk)
        for score, chunk in zip(scores, chunks)
        if not wanted or chunk["doc_id"] in wanted
    ]
    if not scored and wanted:
        # Stage 1 pointed at docs we have not ingested — fall back to the whole corpus
        # rather than returning nothing to a technician standing at the machine.
        scored = [(float(s), c) for s, c in zip(scores, chunks)]

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[: max(1, int(k))]

    results = [
        {
            "doc_id": chunk["doc_id"],
            "doc_title": chunk.get("doc_title"),
            "page": chunk.get("page"),
            "text": chunk.get("text", ""),
            "figures": chunk.get("figures", []),
            "safety": bool(chunk.get("safety")),
            "score": round(score, 4),
        }
        for score, chunk in top
    ]
    return {
        "query": query,
        "restricted_to_doc_ids": sorted(wanted) or None,
        "results": results,
    }
