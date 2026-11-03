"""Stage 4 — match: for each active reader, select today's problems in their beats and coverage area, rank them,
and match publishable cards to each. Results go to `match_results` (rewritten per reader/day → idempotent).

Coverage rules:
* county/state readers: problems whose FIPS fall in their area (a county in a covered state counts; a statewide
  problem counts for readers covering any county in that state), plus national problems;
* "US" (national) readers: national problems plus any problem of severity ≥ NATIONAL_SEVERITY.
"""

from __future__ import annotations

import logging
from collections import Counter

import logfire
from sqlalchemy import delete, select

from solbrief.db import session_scope
from solbrief.db.models import MatchResult, Problem, Reader, RunStatus
from solbrief.embed import voyage_embedder
from solbrief.matcher import MatchQuery, match
from solbrief.pipeline.stages import StageContext, StageResult, stage
from solbrief.taxonomy import load_snapshot

log = logging.getLogger(__name__)
PROBLEMS_PER_READER = 5
CARDS_PER_PROBLEM = 3
NATIONAL_SEVERITY = 0.75


def expand_beats(beats: list[str]) -> set[str]:
    """A beat on a parent class covers its children too; a child beat covers only itself."""
    _, classes = load_snapshot("v1")
    return set(beats) | {c.id for c in classes if c.parent_id in beats}


def in_coverage(problem: Problem, reader: Reader) -> bool:
    if reader.is_national:
        return problem.is_national or (problem.severity or 0) >= NATIONAL_SEVERITY
    if problem.is_national:
        return True
    cov = set(reader.coverage_fips)
    cov_states = {f[:2] for f in cov}
    for f in problem.fips:
        if f in cov:
            return True
        if len(f) == 5 and f[:2] in cov:  # county inside a covered state
            return True
        if len(f) == 2 and f in cov_states:  # statewide problem, reader covers part of that state
            return True
    return False


def problem_priority(p: Problem) -> float:
    return (
        0.5 * (p.severity if p.severity is not None else 0.4) + 0.3 * (p.novelty or 0) + 0.2 * (p.locality_score or 0)
    )


def select_problems(problems: list[Problem], reader: Reader) -> list[Problem]:
    beats = expand_beats(list(reader.beats))
    chosen = [p for p in problems if (not beats or beats & set(p.problem_class_ids)) and in_coverage(p, reader)]
    chosen.sort(key=lambda p: (-problem_priority(p), p.title))
    return chosen[:PROBLEMS_PER_READER]


@stage("match")
def match_stage(ctx: StageContext) -> StageResult:
    embedder = voyage_embedder()
    counts: Counter = Counter()
    with session_scope() as s:
        readers = s.scalars(select(Reader).where(Reader.status == "active")).all()
        problems = s.scalars(select(Problem).where(Problem.run_date == ctx.run_date)).all()
        counts["readers"] = len(readers)
        counts["problems_today"] = len(problems)
        for reader in readers:
            with logfire.span("match reader {reader_id}", reader_id=str(reader.id)):
                s.execute(
                    delete(MatchResult).where(MatchResult.run_date == ctx.run_date, MatchResult.reader_id == reader.id)
                )
                chosen = select_problems(problems, reader)
                counts["problems_selected"] += len(chosen)
                for p_rank, p in enumerate(chosen, start=1):
                    fips = list(p.fips) if p.fips else list(reader.coverage_fips)
                    outcome = match(s, MatchQuery(problem_id=p.id, fips=fips, k=CARDS_PER_PROBLEM), embedder=embedder)
                    if not outcome.cards:
                        counts["problems_without_cards" if outcome.item_type == "solution" else "service_problems"] += 1
                        s.add(
                            MatchResult(
                                run_date=ctx.run_date,
                                reader_id=reader.id,
                                problem_id=p.id,
                                problem_rank=p_rank,
                                features={"item_type": outcome.item_type, "signals": outcome.signals},
                            )
                        )
                        continue
                    for c_rank, rc in enumerate(outcome.cards, start=1):
                        s.add(
                            MatchResult(
                                run_date=ctx.run_date,
                                reader_id=reader.id,
                                problem_id=p.id,
                                problem_rank=p_rank,
                                card_id=rc.card_id,
                                card_rank=c_rank,
                                score=rc.score,
                                features=rc.features | {"signals": outcome.signals},
                            )
                        )
                        counts["cards_matched"] += 1
    counts["semantic_available"] = int(embedder is not None)
    return StageResult(status=RunStatus.succeeded, counts=dict(counts))
