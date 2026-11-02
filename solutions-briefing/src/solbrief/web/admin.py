"""Admin API — bearer ADMIN_TOKEN. Trigger a run, check run status, re-run a stage, dry-run.

If ADMIN_TOKEN is unset the admin API is disabled (503), never open.
"""

from __future__ import annotations

import datetime as dt
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from solbrief.config import get_settings
from solbrief.db import session_scope
from solbrief.db.models import Run
from solbrief.pipeline.stages import STAGE_ORDER


def require_admin(authorization: str | None = Header(default=None)) -> None:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(503, "admin API disabled: ADMIN_TOKEN not set")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(401, "invalid admin token", headers={"WWW-Authenticate": "Bearer"})


router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


class RunRequest(BaseModel):
    date: dt.date | None = None
    dry_run: bool = True
    force: bool = False


def _effective_dry_run(requested: bool) -> bool:
    # Staging can never send, whatever the caller asks.
    return get_settings().app_env == "staging" or requested


@router.post("/runs", status_code=202)
async def trigger_run(req: RunRequest) -> dict:
    from procrastinate.exceptions import AlreadyEnqueued

    from solbrief.alerts import local_today
    from solbrief.queue import daily_run_lock, daily_run_task

    run_date = req.date or local_today()
    dry_run = _effective_dry_run(req.dry_run)
    try:
        job_id = await daily_run_task.configure(queueing_lock=daily_run_lock(run_date, dry_run)).defer_async(
            run_date=run_date.isoformat(), dry_run=dry_run, force=req.force
        )
    except AlreadyEnqueued:
        raise HTTPException(
            409, f"a daily run for {run_date} (dry_run={dry_run}) is already queued"
        ) from None
    return {"job_id": job_id, "date": str(run_date), "dry_run": dry_run}


@router.post("/runs/{run_date}/stages/{stage}/rerun", status_code=202)
async def rerun_stage(run_date: dt.date, stage: str, dry_run: bool = True) -> dict:
    from solbrief.queue import rerun_stage_task

    if stage not in STAGE_ORDER:
        raise HTTPException(404, f"unknown stage {stage!r}; stages: {', '.join(STAGE_ORDER)}")
    dry_run = _effective_dry_run(dry_run)
    job_id = await rerun_stage_task.defer_async(run_date=run_date.isoformat(), stage=stage, dry_run=dry_run)
    return {"job_id": job_id, "date": str(run_date), "stage": stage, "dry_run": dry_run}


@router.get("/runs/{run_date}")
def run_status(run_date: dt.date) -> dict:
    with session_scope() as s:
        rows = s.scalars(select(Run).where(Run.run_date == run_date).order_by(Run.started_at)).all()
        return {
            "date": str(run_date),
            "stages": [
                {
                    "stage": r.stage,
                    "dry_run": r.dry_run,
                    "status": r.status.value,
                    "attempt": r.attempt,
                    "counts": r.counts,
                    "errors": r.errors,
                    "trace_id": r.trace_id,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
                for r in rows
            ],
        }
