"""Stage 2 — library research: for today's problem classes where publishable card coverage is thin, draft new
cards from unused seed sources. Drafts are stored unverified; the verify stage (separate context) decides.

Without ANTHROPIC_API_KEY the stage reports `skipped` and still records which classes are thin and which have
no seed sources yet (useful for curating seeds).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import date

import logfire
from sqlalchemy import func, select

from solbrief.agents.drafter import draft_card
from solbrief.config import get_settings
from solbrief.db import session_scope
from solbrief.db.models import CardEvidence, ItemType, Problem, RunStatus, SolutionCard
from solbrief.library.draft import store_draft
from solbrief.library.seeds import Seed, seeds_for
from solbrief.pipeline.stages import StageContext, StageResult, stage

log = logging.getLogger(__name__)


def thin_classes(run_date: date) -> list[tuple[str, int, int]]:
    """(class_id, problems today, publishable cards) for solution-type problem classes, thinnest first."""
    min_cards = get_settings().library_min_cards_per_class
    with session_scope() as s:
        demand: Counter = Counter()
        for classes, sev in s.execute(
            select(Problem.problem_class_ids, Problem.severity).where(
                Problem.run_date == run_date, Problem.item_type == ItemType.solution
            )
        ):
            for c in classes:
                demand[c] += 1 + (sev or 0)
        out = []
        for cls, weight in demand.most_common():
            have = s.scalar(
                select(func.count())
                .select_from(SolutionCard)
                .where(SolutionCard.publishable.is_(True), SolutionCard.problem_class_ids.any(cls))
            )
            if have < min_cards:
                out.append((cls, round(weight, 2), have))
    return out


def unused_seeds(class_id: str) -> list[Seed]:
    with session_scope() as s:
        cited = set(s.scalars(select(CardEvidence.source_url).distinct()))
    return [seed for seed in seeds_for(class_id) if seed.url not in cited]


def draft_from_seeds(class_id: str, limit: int, run_id=None) -> Counter:
    counts: Counter = Counter()
    for seed in unused_seeds(class_id)[:limit]:
        with logfire.span("draft card {class_id} from {seed}", class_id=class_id, seed=seed.url):
            try:
                result = asyncio.run(draft_card(seed, class_id))
            except Exception:
                log.exception("drafter crashed on %s", seed.url)
                counts["error"] += 1
                continue
            if result.cost_usd:
                counts["cost_usd_x100"] += round(result.cost_usd * 100)
            if result.draft is None:
                log.warning("no draft from %s: %s", seed.url, result.error)
                counts["no_draft"] += 1
                continue
            with session_scope() as s:
                store_draft(s, result.draft, run_id)
            counts["drafted"] += 1
    return counts


@stage("library_research")
def library_research(ctx: StageContext) -> StageResult:
    s = get_settings()
    thin = thin_classes(ctx.run_date)
    no_seeds = [c for c, _, _ in thin if not seeds_for(c)]
    with_seeds = [c for c, _, _ in thin if seeds_for(c)]
    counts: dict = {"thin_classes": [c for c, _, _ in thin], "no_seed_sources": no_seeds}
    if not s.anthropic_api_key:
        counts["reason"] = "ANTHROPIC_API_KEY not set; drafting skipped"
        return StageResult(status=RunStatus.skipped, counts=counts)
    totals: Counter = Counter()
    for cls in with_seeds[: s.library_classes_per_run]:
        totals.update(draft_from_seeds(cls, s.library_drafts_per_class))
    counts.update(dict(totals))
    status = RunStatus.partial if totals["error"] else RunStatus.succeeded
    return StageResult(status=status, counts=counts)
