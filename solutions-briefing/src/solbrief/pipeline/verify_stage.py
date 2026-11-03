"""Stage 3 — verify: check new drafts, and re-verify cards whose last check is older than the threshold.

Runs in its own context: it receives card rows and URLs only, never the drafter's reasoning.
New drafts use the shared fetch cache (same text the drafter saw); re-verification forces fresh fetches
so disappeared or changed sources are detected and the card goes stale.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

import logfire
from sqlalchemy import or_, select

from solbrief.adapters.base import PoliteClient
from solbrief.config import get_settings
from solbrief.db import session_scope
from solbrief.db.models import CardStatus, RunStatus, SolutionCard
from solbrief.pipeline.stages import StageContext, StageResult, stage
from solbrief.verify import http_fetcher, verify_card
from solbrief.verify.entailment import anthropic_entailment

log = logging.getLogger(__name__)
MAX_CARDS_PER_RUN = 200


def _due(now: datetime) -> tuple[list, list]:
    cutoff = (now - timedelta(days=get_settings().card_reverify_days)).isoformat()
    with session_scope() as s:
        new = s.scalars(
            select(SolutionCard.id)
            .where(SolutionCard.verification.is_(None), SolutionCard.status != CardStatus.retracted)
            .order_by(SolutionCard.created_at)
            .limit(MAX_CARDS_PER_RUN)
        ).all()
        old = s.scalars(
            select(SolutionCard.id)
            .where(
                SolutionCard.verification.is_not(None),
                SolutionCard.status.in_([CardStatus.verified, CardStatus.provisional]),
                or_(SolutionCard.verification["checked_at"].astext < cutoff),
            )
            .limit(MAX_CARDS_PER_RUN)
        ).all()
    return list(new), list(old)


def run_verification(card_ids, fetch, label: str, entails=None) -> Counter:
    counts: Counter = Counter()
    for card_id in card_ids:
        with logfire.span("verify card {card_id}", card_id=str(card_id), kind=label):
            try:
                with session_scope() as s:
                    card = s.get(SolutionCard, card_id)
                    report = verify_card(s, card, fetch, entails)
                counts[f"{label}:{report.status}"] += 1
                counts[f"{label}:publishable"] += int(report.publishable)
                for sup in report.supports + report.limitations:
                    if not sup["ok"]:
                        counts[f"dropped:{sup['reason']}"] += 1
            except Exception:
                log.exception("verification failed for card %s", card_id)
                counts[f"{label}:error"] += 1
    return counts


@stage("verify")
def verify(ctx: StageContext) -> StageResult:
    now = datetime.now(UTC)
    new, old = _due(now)
    client = PoliteClient()
    try:
        entails = anthropic_entailment()
        counts = run_verification(new, http_fetcher(client), "new", entails)
        counts += run_verification(old, http_fetcher(client, max_age=timedelta(0)), "reverify", entails)
    finally:
        client.close()
    errors = [{"msg": f"{n} card(s) errored"} for k, n in counts.items() if k.endswith(":error")]
    status = RunStatus.partial if errors else RunStatus.succeeded
    counts_out = {"new": len(new), "reverify": len(old), "entailment_check": entails is not None, **dict(counts)}
    return StageResult(status=status, counts=counts_out, errors=errors)
