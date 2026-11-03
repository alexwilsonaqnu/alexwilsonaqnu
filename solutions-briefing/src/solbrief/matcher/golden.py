"""Golden set for the matcher: reporter-style problem statements → the cards a good search should return.

* `evals/matcher_golden/golden.yaml` — pairs with `status: proposed | confirmed | rejected`. Drafted by us,
  confirmed by Knight Lab editors. CI gates only on confirmed pairs (threshold stored in the file).
* `evals/matcher_golden/library.json` — frozen snapshot of the cards (and query embeddings) the pairs refer to,
  so the eval runs offline and deterministically in CI. Regenerate with `solbrief golden-snapshot`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import select

from solbrief.db.models import CardStatus, EvidenceGrade, ItemType, SolutionCard
from solbrief.matcher import MatchQuery, match, recall_at_k

GOLDEN_DIR = Path(__file__).resolve().parents[3] / "evals" / "matcher_golden"


@dataclass
class Pair:
    id: str
    query: str
    expected: list[str]
    status: str
    fips: list[str]


def load_pairs(path: Path | None = None) -> tuple[dict, list[Pair]]:
    doc = yaml.safe_load((path or GOLDEN_DIR / "golden.yaml").read_text(encoding="utf-8"))
    pairs = [
        Pair(p["id"], p["query"], list(p.get("expected", [])), p.get("status", "proposed"), list(p.get("fips", [])))
        for p in doc.get("pairs", [])
    ]
    return doc, [p for p in pairs if p.status != "rejected"]


def load_snapshot(session, path: Path | None = None) -> dict:
    """Insert the snapshot's cards into the (test) database. Returns the snapshot dict."""
    snap = json.loads((path or GOLDEN_DIR / "library.json").read_text(encoding="utf-8"))
    for c in snap["cards"]:
        session.add(
            SolutionCard(
                id=uuid.UUID(c["id"]) if c.get("id") else uuid.uuid4(),
                slug=c["slug"],
                title=c["title"],
                item_type=ItemType.solution,
                response_what=c["response_what"],
                insight_mechanism=c.get("insight_mechanism"),
                insight_transferability=c.get("insight_transferability"),
                problem_class_ids=c["problem_class_ids"],
                response_where_fips=c.get("response_where_fips", []),
                status=CardStatus(c.get("status", "verified")),
                publishable=True,
                evidence_grade=EvidenceGrade(c["evidence_grade"]) if c.get("evidence_grade") else None,
                last_verified_at=datetime.fromisoformat(c["last_verified_at"]) if c.get("last_verified_at") else None,
                embedding=c.get("embedding"),
                embedding_model=snap.get("embedding_model") if c.get("embedding") else None,
            )
        )
    session.flush()
    return snap


def snapshot_embedder(snap: dict, pairs: list[Pair]):
    """Embedder that serves precomputed query vectors from the snapshot (None if the snapshot has none)."""
    vectors = snap.get("query_embeddings") or {}
    if not vectors:
        return None
    by_text = {p.query: vectors[p.id] for p in pairs if p.id in vectors}

    def embed(texts, input_type):
        return [by_text[t] for t in texts]

    return embed


@dataclass
class EvalResult:
    recall_all: float
    recall_confirmed: float | None
    per_pair: list[dict]


def evaluate(session, pairs: list[Pair], embedder=None, weights=None, k: int = 5) -> EvalResult:
    now = datetime.now(UTC)
    results, per_pair = [], []
    for p in pairs:
        out = match(session, MatchQuery(text=p.query, fips=p.fips, k=k), embedder=embedder, weights=weights, now=now)
        got = [c.slug for c in out.cards]
        results.append(got)
        per_pair.append(
            {
                "id": p.id,
                "status": p.status,
                "expected": p.expected,
                "got": got,
                "hit": len(set(got) & set(p.expected)) / len(p.expected) if p.expected else None,
            }
        )
    confirmed = [(r, p.expected) for r, p in zip(results, pairs, strict=True) if p.status == "confirmed"]
    return EvalResult(
        recall_all=recall_at_k(results, [p.expected for p in pairs], k),
        recall_confirmed=recall_at_k([r for r, _ in confirmed], [e for _, e in confirmed], k) if confirmed else None,
        per_pair=per_pair,
    )


def fit_weights(
    session, pairs: list[Pair], start: dict[str, float], embedder=None, steps=(0.0, 0.5, 1.0, 1.5, 2.0), rounds: int = 3
) -> tuple[dict[str, float], float, float]:
    """Coordinate search: scale one weight at a time, keep changes that improve recall@5 (ties: keep current)."""
    best = dict(start)
    base = evaluate(session, pairs, embedder, best).recall_all
    score = base
    for _ in range(rounds):
        improved = False
        for name in [n for n in best if n != "feedback"]:  # feedback penalty is policy, not fitted
            for factor in steps:
                trial = dict(best)
                trial[name] = round(start[name] * factor, 4)
                r = evaluate(session, pairs, embedder, trial).recall_all
                if r > score + 1e-9:
                    best, score, improved = trial, r, True
        if not improved:
            break
    return best, base, score


def build_snapshot(session, pairs: list[Pair], embedder=None, embedding_model: str | None = None) -> dict:
    """Snapshot every publishable card referenced by the pairs (plus the rest of the publishable library as
    distractors) and, if an embedder is available, the pairs' query embeddings."""
    cards = session.scalars(select(SolutionCard).where(SolutionCard.publishable.is_(True))).all()
    snap = {
        "kind": "live",
        "generated_at": datetime.now(UTC).isoformat(),
        "embedding_model": embedding_model,
        "cards": [
            {
                "id": str(c.id),
                "slug": c.slug,
                "title": c.title,
                "response_what": c.response_what,
                "insight_mechanism": c.insight_mechanism,
                "insight_transferability": c.insight_transferability,
                "problem_class_ids": list(c.problem_class_ids),
                "response_where_fips": list(c.response_where_fips),
                "status": c.status.value,
                "evidence_grade": c.evidence_grade.value if c.evidence_grade else None,
                "last_verified_at": c.last_verified_at.isoformat() if c.last_verified_at else None,
                "embedding": [round(float(x), 6) for x in c.embedding] if c.embedding is not None else None,
            }
            for c in cards
        ],
        "query_embeddings": {},
    }
    if embedder is not None:
        vecs = embedder([p.query for p in pairs], "query")
        snap["query_embeddings"] = {p.id: [round(float(x), 6) for x in v] for p, v in zip(pairs, vecs, strict=True)}
    return snap
