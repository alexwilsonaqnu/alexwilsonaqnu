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

# A token mixing letters and digits is an identifier a technician read off a display or a
# parts label: a fault code (F7E4), a part number (W11035747), a model (WTW5057LW0).
_IDENTIFIER_RE = re.compile(r"^(?=.*[a-z])(?=.*\d)[a-z0-9]{4,}$")

# Multiples of the top BM25 score, awarded per distinct query identifier the chunk
# contains. Plain BM25 ranks a fault-code question by its *common* words: "what does fault
# code F7E1 mean on this washer" scored a chunk about fault-code *history* above the chunk
# that defines F7E1, because "fault" and "code" appear more often there and one rare token
# cannot outweigh them. A technician reading a code off the console is the single most
# common query this system takes, and the chunk naming that code is essentially always the
# answer, so an exact identifier match has to dominate the ranking rather than nudge it.
# Scaling to the top score keeps this corpus-independent; BM25 still orders the chunks that
# all match the identifier.
IDENTIFIER_BOOST = 1.0

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


def _boosted_scores(scores, query_tokens: list[str], chunks: list[dict[str, Any]]) -> list[float]:
    """Add an exact-identifier bonus on top of BM25. See IDENTIFIER_BOOST."""
    wanted = {t for t in query_tokens if _IDENTIFIER_RE.match(t)}
    if not wanted:
        return [float(s) for s in scores]
    unit = IDENTIFIER_BOOST * max(max((float(s) for s in scores), default=0.0), 1.0)
    return [
        float(score) + unit * len(wanted & set(tokenize(chunk.get("text", ""))))
        for score, chunk in zip(scores, chunks)
    ]


def _acquire(doc_ids: list[str] | None, models: list[str]) -> tuple[set[str], list[dict[str, Any]]]:
    """Resolve stage-1 doc_ids into searchable local documents, fetching any we lack.

    This is what makes stage-1 routing real rather than decorative: the agent can name any
    document ServiceMatters returned, including one nobody ingested beforehand, and it
    becomes searchable here.
    """
    from src.tools.doc_cache import ensure_local

    resolved: set[str] = set()
    acquisitions: list[dict[str, Any]] = []
    for doc_id in doc_ids or []:
        if not doc_id:
            continue
        local, status = ensure_local(doc_id, models)
        if local:
            resolved.add(local)
        elif status == "unknown":
            # Not a ServiceMatters id we have seen — treat it as a local id directly.
            resolved.add(doc_id)
        if status != "cached":
            acquisitions.append({"doc_id": doc_id, "status": status, "local_doc_id": local})
    return resolved, acquisitions


def _covers(chunk: dict[str, Any], models: list[str]) -> bool:
    """Does this chunk's document cover one of these models?

    Tolerant at the ends because a model number and a document's coverage list disagree
    about the trailing revision digit constantly — WTW5057LW0 on the case, WTW5057LW in
    the coverage list, the same washer.
    """
    covered = [str(m).upper() for m in chunk.get("models", []) if m]
    for wanted in (str(m).upper() for m in models if m):
        for have in covered:
            if wanted == have or wanted.startswith(have) or have.startswith(wanted):
                return True
    return False


def manual_search(
    query: str,
    doc_ids: list[str] | None = None,
    k: int = 5,
    models: list[str] | None = None,
) -> dict[str, Any]:
    """Search ingested manual pages, optionally restricted to stage-1 candidate docs."""
    models = models or []
    wanted, acquisitions = _acquire(doc_ids, models)
    chunks, bm25 = _index()
    if not chunks or bm25 is None:
        return {
            "query": query,
            "results": [],
            "documents": acquisitions,
            "note": "No manual has been ingested yet. Run src.ingest.ingest_pdf first.",
        }

    query_tokens = tokenize(query)
    scores = _boosted_scores(bm25.get_scores(query_tokens), query_tokens, chunks)
    everything = [(float(s), c) for s, c in zip(scores, chunks)]
    scoped_by = None
    if wanted:
        scored = [pair for pair in everything if pair[1]["doc_id"] in wanted]
    elif models:
        # A warmed corpus holds documents for every model the fleet services — 341 of them
        # across 59 models — so an unscoped search answers a WTW5057LW0 question out of
        # some other washer's tech sheet. Whenever the model is known it is the scope.
        scored = [pair for pair in everything if _covers(pair[1], models)]
        scoped_by = models if scored else None
    else:
        scored = everything
    if not scored:
        # Nothing matched the scope — better a wider answer than none to a technician
        # standing at the machine, and the citation still names the document.
        scored = everything

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
        "scoped_to_models": scoped_by,
        # What stage 2 had to do to make those documents readable. `indexing` means a
        # document too large to index inside a turn is being built in the background and
        # will answer on a later search this session.
        "documents": acquisitions,
        "results": results,
    }
