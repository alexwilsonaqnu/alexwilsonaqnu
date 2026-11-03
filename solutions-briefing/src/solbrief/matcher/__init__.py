"""The shared matcher: a problem (from the feed) or a typed problem statement → ranked publishable cards.

The daily briefing, web search and reply-to-query all call `match()`; the only difference is the input.

Candidates are the union of three retrievals over **publishable** cards only (verified or labelled provisional;
never stale/retracted/unverified): taxonomy overlap (incl. parent/child), pgvector nearest neighbours, and
Postgres full-text search. Each candidate gets interpretable features and a weighted score; the features are
returned with the result so reporters and admins can see why a card ranked.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from importlib import resources

import yaml
from sqlalchemy import func, select

from solbrief.classify import LLMClassifier, rule_classes
from solbrief.db.models import CardStatus, Feedback, FeedbackSignal, ItemType, Problem, SolutionCard
from solbrief.embed import Embedder, problem_text
from solbrief.geo import place_similarity
from solbrief.taxonomy import class_ids, load_snapshot
from solbrief.verify.sources import GRADE_ORDER

CANDIDATES_PER_RETRIEVER = 50
LIVE_STATUSES = (CardStatus.verified, CardStatus.provisional)
_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset("the and for with that this from are was were has have had not but its into over more".split())


@cache
def default_weights() -> dict[str, float]:
    doc = yaml.safe_load(resources.files("solbrief.matcher").joinpath("weights.yaml").read_text(encoding="utf-8"))
    return {k: float(v) for k, v in doc["weights"].items()}


@cache
def _family() -> dict[str, set[str]]:
    """class id → its parent and children (for partial credit)."""
    _, classes = load_snapshot("v1")
    fam: dict[str, set[str]] = {c.id: set() for c in classes}
    for c in classes:
        if c.parent_id:
            fam[c.id].add(c.parent_id)
            fam[c.parent_id].add(c.id)
    return fam


@dataclass
class MatchQuery:
    text: str | None = None
    problem_id: uuid.UUID | None = None
    fips: list[str] = field(default_factory=list)
    class_ids: list[str] | None = None
    k: int = 5


@dataclass
class RankedCard:
    card_id: uuid.UUID
    slug: str
    title: str
    status: str
    score: float
    features: dict


@dataclass
class MatchOutcome:
    classes: list[str]
    cards: list[RankedCard]
    item_type: str = "solution"
    signals: dict = field(default_factory=dict)  # which retrievers were available


def class_score(card_classes: list[str], query_classes: list[str]) -> float:
    if not query_classes:
        return 0.0
    if set(card_classes) & set(query_classes):
        return 1.0
    fam = _family()
    related = set().union(*(fam.get(c, set()) for c in query_classes))
    return 0.5 if set(card_classes) & related else 0.0


def grade_score(grade) -> float:
    return round(GRADE_ORDER.index(grade) / (len(GRADE_ORDER) - 1), 4) if grade else 0.0


def recency_score(last_verified_at: datetime | None, now: datetime) -> float:
    if last_verified_at is None:
        return 0.5  # provisional cards haven't been confirmed by two publishers; neutral, not penalized twice
    age_days = (now - last_verified_at).days
    return 1.0 if age_days <= 180 else max(0.0, 1.0 - (age_days - 180) / 550)


def _or_tsquery(text: str) -> str | None:
    words = [w for w in _WORD.findall(text.lower()) if w not in _STOP]
    return " | ".join(dict.fromkeys(words)) or None


def _query_inputs(session, q: MatchQuery, embedder: Embedder | None, classifier: LLMClassifier | None):
    if q.problem_id is not None:
        p = session.get(Problem, q.problem_id)
        if p is None:
            raise LookupError(f"problem {q.problem_id} not found")
        vec = list(p.embedding) if p.embedding is not None else None
        if vec is None and embedder is not None:
            vec = embedder([problem_text(p)], "document")[0]
        return problem_text(p), list(p.problem_class_ids), q.fips or list(p.fips), vec, p.item_type.value
    text = (q.text or "").strip()
    classes = q.class_ids if q.class_ids is not None else rule_classes(text)
    if not classes and classifier is not None:
        try:
            classes = [c for c in classifier(text) if c in class_ids()][:3]
        except Exception:
            classes = []
    vec = embedder([text], "query")[0] if (embedder is not None and text) else None
    return text, classes, q.fips, vec, ItemType.solution.value


def match(
    session,
    q: MatchQuery,
    embedder: Embedder | None = None,
    classifier: LLMClassifier | None = None,
    weights: dict[str, float] | None = None,
    now: datetime | None = None,
) -> MatchOutcome:
    weights = weights or default_weights()
    now = now or datetime.now(UTC)
    text, classes, fips, vec, item_type = _query_inputs(session, q, embedder, classifier)
    signals = {"semantic": vec is not None, "classes": bool(classes), "lexical": bool(text)}
    if item_type == ItemType.service.value:
        # Service guidance comes from agency sources (M5), never from solution cards. Never blur the two.
        return MatchOutcome(classes, [], item_type, signals)

    live = (SolutionCard.publishable.is_(True), SolutionCard.status.in_(LIVE_STATUSES))
    ids: set[uuid.UUID] = set()
    if classes:
        expanded = sorted(set(classes).union(*(_family().get(c, set()) for c in classes)))
        ids.update(
            session.scalars(select(SolutionCard.id).where(*live, SolutionCard.problem_class_ids.overlap(expanded)))
        )
    distances: dict[uuid.UUID, float] = {}
    if vec is not None:
        dist = SolutionCard.embedding.cosine_distance(vec)
        for cid, d in session.execute(
            select(SolutionCard.id, dist)
            .where(*live, SolutionCard.embedding.is_not(None))
            .order_by(dist)
            .limit(CANDIDATES_PER_RETRIEVER)
        ):
            distances[cid] = float(d)
            ids.add(cid)
    ranks: dict[uuid.UUID, float] = {}
    tsq_text = _or_tsquery(text) if text else None
    if tsq_text:
        tsq = func.to_tsquery("english", tsq_text)
        rank = func.ts_rank_cd(SolutionCard.search_tsv, tsq)
        for cid, r in session.execute(
            select(SolutionCard.id, rank)
            .where(*live, SolutionCard.search_tsv.op("@@")(tsq))
            .order_by(rank.desc())
            .limit(CANDIDATES_PER_RETRIEVER)
        ):
            ranks[cid] = float(r)
            ids.add(cid)
    if not ids:
        return MatchOutcome(classes, [], item_type, signals)

    cards = session.scalars(select(SolutionCard).where(SolutionCard.id.in_(ids))).all()
    if vec is not None:  # distances for class/lexical-only candidates too
        missing = [c.id for c in cards if c.id not in distances and c.embedding is not None]
        if missing:
            dist = SolutionCard.embedding.cosine_distance(vec)
            distances.update(
                {
                    cid: float(d)
                    for cid, d in session.execute(select(SolutionCard.id, dist).where(SolutionCard.id.in_(missing)))
                }
            )
    feedback = dict(
        session.execute(
            select(Feedback.card_id, func.count())
            .where(
                Feedback.card_id.in_(ids),
                Feedback.signal.in_([FeedbackSignal.bad_lead, FeedbackSignal.already_covered]),
            )
            .group_by(Feedback.card_id)
        ).all()
    )
    max_rank = max(ranks.values(), default=0.0) or 1.0

    ranked: list[RankedCard] = []
    for c in cards:
        f = {
            "semantic": round(1.0 - distances[c.id], 4) if c.id in distances else 0.0,
            "class": class_score(c.problem_class_ids, classes),
            "lexical": round(ranks.get(c.id, 0.0) / max_rank, 4),
            "geo": place_similarity(list(c.response_where_fips), list(fips)),
            "grade": grade_score(c.evidence_grade),
            "verified": 1.0 if c.status == CardStatus.verified else 0.0,
            "recency": round(recency_score(c.last_verified_at, now), 4),
            "feedback": min(1.0, feedback.get(c.id, 0) / 3),
        }
        score = sum(weights.get(name, 0.0) * value for name, value in f.items())
        ranked.append(RankedCard(c.id, c.slug, c.title, c.status.value, round(score, 6), f))
    ranked.sort(key=lambda r: (-r.score, r.slug))
    return MatchOutcome(classes, ranked[: q.k], item_type, signals)


def recall_at_k(results: list[list[str]], expected: list[list[str]], k: int = 5) -> float:
    """Mean over queries of |top-k ∩ expected| / |expected| (queries with no expectations are skipped)."""
    scores = [len(set(r[:k]) & set(e)) / len(e) for r, e in zip(results, expected, strict=True) if e]
    return round(sum(scores) / len(scores), 4) if scores else math.nan
