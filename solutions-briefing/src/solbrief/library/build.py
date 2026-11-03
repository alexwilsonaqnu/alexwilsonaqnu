"""Bootstrap the solution library: draft N cards from unused seeds across classes, under a hard total budget.

Budget rule (never exceeded): a new drafting session starts only if `spent + per-card cap ≤ total budget`, where
the per-card cap is `DRAFTER_BUDGET_USD` (each session is itself capped by the SDK's `max_budget_usd`). A session
whose cost is unknown (crash) is charged the full per-card cap. Drafts are stored unverified; the verifier then
runs on them and publishable cards are embedded.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field

import logfire

from solbrief.agents.drafter import draft_card
from solbrief.config import get_settings
from solbrief.db import session_scope
from solbrief.library.draft import store_draft
from solbrief.library.seeds import Seed, all_seeds
from solbrief.pipeline.library_stage import unused_seeds

log = logging.getLogger(__name__)


@dataclass
class BuildReport:
    budget_usd: float
    spent_usd: float = 0.0
    attempted: int = 0
    drafted: int = 0
    failures: Counter = field(default_factory=Counter)
    by_class: Counter = field(default_factory=Counter)
    stopped_because: str = ""


def plan_seeds(target: int) -> list[tuple[str, Seed]]:
    """Round-robin over classes so the library spans many problem types (Community Guide seeds first)."""
    classes = sorted({c for s in all_seeds() for c in s.class_ids})
    queues = {c: [s for s in unused_seeds(c)] for c in classes}
    for q in queues.values():
        q.sort(key=lambda s: (0 if "Community Guide" in s.publisher else 1, s.title))
    plan: list[tuple[str, Seed]] = []
    used: set[str] = set()
    while len(plan) < target and any(queues.values()):
        for c in classes:
            while queues[c] and queues[c][0].url in used:
                queues[c].pop(0)
            if queues[c] and len(plan) < target:
                seed = queues[c].pop(0)
                used.add(seed.url)
                plan.append((c, seed))
    return plan


def build_library(target: int, budget_usd: float | None = None, draft_fn=draft_card) -> BuildReport:
    s = get_settings()
    budget = s.library_build_budget_usd if budget_usd is None else budget_usd
    per_card = s.drafter_budget_usd
    report = BuildReport(budget_usd=budget)
    if budget <= 0:
        report.stopped_because = "LIBRARY_BUILD_BUDGET_USD is 0 — set it to authorize spending"
        return report
    for class_id, seed in plan_seeds(target):
        if report.spent_usd + per_card > budget:
            report.stopped_because = f"budget: ${report.spent_usd:.2f} spent, next card could exceed ${budget:.2f}"
            break
        report.attempted += 1
        with logfire.span("library-build draft {class_id}", class_id=class_id, seed=seed.url):
            try:
                result = asyncio.run(draft_fn(seed, class_id))
            except Exception:
                log.exception("drafter crashed on %s", seed.url)
                report.spent_usd += per_card  # unknown cost → charge the cap
                report.failures["crash"] += 1
                continue
        report.spent_usd += result.cost_usd if result.cost_usd is not None else per_card
        if result.draft is None:
            report.failures[(result.error or "no draft").split(":")[0][:60]] += 1
            continue
        with session_scope() as sess:
            store_draft(sess, result.draft)
        report.drafted += 1
        report.by_class[class_id] += 1
    else:
        report.stopped_because = report.stopped_because or "target reached or seeds exhausted"
    report.spent_usd = round(report.spent_usd, 4)
    return report
