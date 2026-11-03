"""Embeddings (Voyage AI, `voyage-3.5`, 1024 dims) for semantic retrieval in the matcher.

Cards and problems are embedded as documents; search text as a query. Rows record the model that produced
their vector, so a model change re-embeds them. Without VOYAGE_API_KEY embedding is skipped (the matcher then
uses taxonomy + full-text signals only) — reported, never a failure.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date
from typing import Literal

import logfire
from sqlalchemy import or_, select

from solbrief.config import get_settings
from solbrief.db import session_scope
from solbrief.db.models import EMBEDDING_DIM, Problem, SolutionCard
from solbrief.taxonomy import load_snapshot

log = logging.getLogger(__name__)
BATCH = 64

InputType = Literal["document", "query"]
Embedder = Callable[[Sequence[str], InputType], list[list[float]]]


def voyage_embedder() -> Embedder | None:
    s = get_settings()
    if not s.voyage_api_key:
        return None
    import voyageai

    client = voyageai.Client(api_key=s.voyage_api_key, max_retries=3, timeout=60)

    def embed(texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            chunk = list(texts[i : i + BATCH])
            with logfire.span("voyage embed {n} {input_type}", n=len(chunk), input_type=input_type):
                result = client.embed(
                    chunk, model=s.voyage_model, input_type=input_type, output_dimension=EMBEDDING_DIM
                )
            out.extend(result.embeddings)
        return out

    return embed


def _labels() -> dict[str, str]:
    return {c.id: c.label for c in load_snapshot("v1")[1]}


def card_text(card: SolutionCard) -> str:
    labels = _labels()
    classes = ", ".join(labels.get(c, c) for c in card.problem_class_ids)
    return "\n".join(x for x in (card.title, card.response_what, card.insight_mechanism, classes) if x)


def problem_text(problem: Problem) -> str:
    return "\n".join(x for x in (problem.title, problem.summary) if x)


def embed_pending(run_date: date | None = None, embedder: Embedder | None = None) -> dict:
    """Embed publishable cards (and the day's problems) missing a vector from the current model."""
    embedder = embedder or voyage_embedder()
    if embedder is None:
        return {"embedded": 0, "reason": "VOYAGE_API_KEY not set"}
    model = get_settings().voyage_model
    counts = {"cards": 0, "problems": 0}
    with session_scope() as s:
        cards = s.scalars(
            select(SolutionCard).where(
                SolutionCard.publishable.is_(True),
                or_(SolutionCard.embedding.is_(None), SolutionCard.embedding_model.is_distinct_from(model)),
            )
        ).all()
        if cards:
            for card, vec in zip(cards, embedder([card_text(c) for c in cards], "document"), strict=True):
                card.embedding, card.embedding_model = vec, model
            counts["cards"] = len(cards)
        if run_date is not None:
            problems = s.scalars(
                select(Problem).where(
                    Problem.run_date == run_date,
                    or_(Problem.embedding.is_(None), Problem.embedding_model.is_distinct_from(model)),
                )
            ).all()
            if problems:
                for p, vec in zip(problems, embedder([problem_text(p) for p in problems], "document"), strict=True):
                    p.embedding, p.embedding_model = vec, model
                counts["problems"] = len(problems)
    return counts
