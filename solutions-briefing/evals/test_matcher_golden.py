"""Matcher recall@5 on the golden set (CI).

Gate: recall@5 on *confirmed* pairs must be ≥ `threshold_confirmed_recall_at_5` (once set). Proposed pairs are
reported only. While the snapshot is the interim fixture library, a sanity floor applies instead.
"""

from __future__ import annotations

import json

from solbrief.db import session_scope
from solbrief.matcher.golden import evaluate, load_pairs, load_snapshot, snapshot_embedder


def test_matcher_recall_at_5_on_golden_set():
    doc, pairs = load_pairs()
    with session_scope() as s:
        snap = load_snapshot(s)
        result = evaluate(s, pairs, embedder=snapshot_embedder(snap, pairs))
        s.rollback()  # leave the test DB clean
    misses = [p for p in result.per_pair if p["hit"] is not None and p["hit"] < 1]
    print(
        f"\nmatcher recall@5: all={result.recall_all} confirmed={result.recall_confirmed} "
        f"({len(pairs)} pairs, library={snap.get('kind')})"
    )
    for m in misses:
        print("  miss", json.dumps(m))
    threshold = doc.get("threshold_confirmed_recall_at_5")
    if threshold is not None and result.recall_confirmed is not None:
        assert result.recall_confirmed >= threshold, f"confirmed recall@5 {result.recall_confirmed} < {threshold}"
    if snap.get("kind") == "fixture":
        assert result.recall_all >= doc["fixture_min_recall_at_5"]
