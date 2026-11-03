"""Daily pipeline orchestrator (plain Python — the orchestrator is not an LLM).

Guarantees:
* One OTel trace per run; its trace_id is written to every `runs` row of that run.
* Only one run per (date, dry_run) executes at a time (Postgres advisory lock).
* A completed run is not repeated unless `force=True` (scheduler double-fire is harmless).
* A failing stage is recorded and the run continues; the run is then `partial`, never silently `succeeded`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

import logfire
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from solbrief.db import get_engine, session_scope
from solbrief.db.models import Run, RunStatus
from solbrief.pipeline import ingest as _ingest  # noqa: F401  (registers stages on import)
from solbrief.pipeline import library_stage as _library  # noqa: F401
from solbrief.pipeline import match_stage as _match  # noqa: F401
from solbrief.pipeline import stages
from solbrief.pipeline import verify_stage as _verify  # noqa: F401
from solbrief.pipeline.stages import STAGE_ORDER, StageContext, StageResult
from solbrief.telemetry import current_trace_id

log = logging.getLogger(__name__)

TERMINAL_OK = (RunStatus.succeeded, RunStatus.partial)


@dataclass
class DailyRunOutcome:
    run_date: date
    dry_run: bool
    status: str  # 'completed' | 'partial' | 'already_done' | 'locked'
    trace_id: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def record_stage(run_date: date, stage: str, dry_run: bool, trace_id: str | None, fn_result=None) -> None:
    """Upsert the `runs` row for a stage. Called at start (running) and end (result)."""
    with session_scope() as s:
        if fn_result is None:
            stmt = insert(Run).values(
                run_date=run_date,
                stage=stage,
                dry_run=dry_run,
                status=RunStatus.running,
                trace_id=trace_id,
                started_at=_now(),
                counts={},
                errors=[],
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_run_day_stage",
                set_={
                    "status": RunStatus.running,
                    "trace_id": trace_id,
                    "started_at": _now(),
                    "finished_at": None,
                    "counts": {},
                    "errors": [],
                    "attempt": Run.attempt + 1,
                },
            )
            s.execute(stmt)
        else:
            row = s.scalars(
                select(Run).where(Run.run_date == run_date, Run.stage == stage, Run.dry_run == dry_run)
            ).one()
            row.status = fn_result.status
            row.counts = fn_result.counts
            row.errors = fn_result.errors
            row.finished_at = _now()


def run_stage(name: str, run_date: date, dry_run: bool) -> StageResult:
    trace_id = current_trace_id()
    record_stage(run_date, name, dry_run, trace_id)
    with logfire.span("stage {stage}", stage=name, run_date=str(run_date), dry_run=dry_run):
        try:
            result = stages.get_stage(name)(StageContext(run_date=run_date, dry_run=dry_run))
        except Exception as exc:  # recorded, not swallowed: run becomes partial
            log.exception("stage %s failed", name)
            logfire.exception("stage {stage} failed", stage=name)
            result = StageResult(status=RunStatus.failed, errors=[{"type": type(exc).__name__, "msg": str(exc)}])
    record_stage(run_date, name, dry_run, trace_id, result)
    return result


def _run_report(run_date: date, dry_run: bool) -> StageResult:
    with session_scope() as s:
        rows = s.scalars(
            select(Run).where(Run.run_date == run_date, Run.dry_run == dry_run, Run.stage != "run_report")
        ).all()
    by_stage = {r.stage: {"status": r.status.value, "counts": r.counts, "errors": len(r.errors)} for r in rows}
    failed = [r.stage for r in rows if r.status == RunStatus.failed]
    return StageResult(
        status=RunStatus.partial if failed else RunStatus.succeeded,
        counts={"stages": by_stage, "failed_stages": failed},
    )


stages.stage("run_report")(lambda ctx: _run_report(ctx.run_date, ctx.dry_run))


def report_status(run_date: date, dry_run: bool) -> RunStatus | None:
    with session_scope() as s:
        return s.scalar(
            select(Run.status).where(Run.run_date == run_date, Run.dry_run == dry_run, Run.stage == "run_report")
        )


def is_complete(run_date: date, dry_run: bool) -> bool:
    return report_status(run_date, dry_run) in TERMINAL_OK


def run_daily(run_date: date, dry_run: bool, force: bool = False) -> DailyRunOutcome:
    lock_key = f"daily_run:{run_date.isoformat()}:{int(dry_run)}"
    with get_engine().connect() as lock_conn:
        got = lock_conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": lock_key}).scalar()
        if not got:
            return DailyRunOutcome(run_date, dry_run, "locked")
        try:
            if not force and is_complete(run_date, dry_run):
                return DailyRunOutcome(run_date, dry_run, "already_done")
            with logfire.span("daily_run {run_date}", run_date=str(run_date), dry_run=dry_run):
                trace_id = current_trace_id()
                for name in STAGE_ORDER:
                    run_stage(name, run_date, dry_run)
            ok = report_status(run_date, dry_run) == RunStatus.succeeded
            final = "completed" if ok else "partial"
            return DailyRunOutcome(run_date, dry_run, final, trace_id)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": lock_key})
            lock_conn.commit()
