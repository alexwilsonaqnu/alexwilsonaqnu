"""Postgres-backed job queue (procrastinate). The web process only ever enqueues; the worker executes.

Queues:
  pipeline — daily run and stage re-runs (one at a time per date via queueing locks)
  reply    — reply-to-query mini-briefings (M6)
  email    — outbound sends (M5)
"""

from __future__ import annotations

from datetime import date

import procrastinate

from solbrief.config import get_settings

app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=get_settings().libpq_url),
)


@app.task(name="daily_run", queue="pipeline", retry=procrastinate.RetryStrategy(max_attempts=2, wait=300))
def daily_run_task(run_date: str, dry_run: bool, force: bool = False) -> dict:
    from solbrief.pipeline.daily import run_daily

    outcome = run_daily(date.fromisoformat(run_date), dry_run=dry_run, force=force)
    return {"status": outcome.status, "trace_id": outcome.trace_id}


@app.task(name="rerun_stage", queue="pipeline")
def rerun_stage_task(run_date: str, stage: str, dry_run: bool) -> dict:
    import logfire

    from solbrief.pipeline.daily import run_stage

    with logfire.span("rerun_stage {stage}", stage=stage, run_date=run_date, dry_run=dry_run):
        result = run_stage(stage, date.fromisoformat(run_date), dry_run)
    return {"status": result.status.value}


def daily_run_lock(run_date: date, dry_run: bool) -> str:
    """Queueing lock: at most one *queued* daily run per (date, dry_run)."""
    return f"daily_run:{run_date.isoformat()}:{int(dry_run)}"
