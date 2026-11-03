"""Admin API — bearer ADMIN_TOKEN. Trigger a run, check run status, re-run a stage, inspect problems.

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
from solbrief.db.models import Problem, Run
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
        raise HTTPException(409, f"a daily run for {run_date} (dry_run={dry_run}) is already queued") from None
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


@router.get("/problems/{run_date}")
def problems(run_date: dt.date, limit: int = 100, class_id: str | None = None, fips: str | None = None) -> dict:
    """A day's problem feed, ranked by severity then locality (the matcher's ranking arrives in M4)."""
    from solbrief.geo import describe

    with session_scope() as s:
        q = select(Problem).where(Problem.run_date == run_date)
        if class_id:
            q = q.where(Problem.problem_class_ids.any(class_id))
        if fips:
            q = q.where(Problem.fips.any(fips))
        q = q.order_by(Problem.severity.desc().nulls_last(), Problem.locality_score.desc()).limit(min(limit, 500))
        rows = s.scalars(q).all()
        return {
            "date": str(run_date),
            "count": len(rows),
            "problems": [
                {
                    "id": str(r.id),
                    "cluster_id": str(r.cluster_id),
                    "title": r.title,
                    "summary": r.summary,
                    "item_type": r.item_type.value,
                    "source": r.source_adapter,
                    "classes": r.problem_class_ids,
                    "places": [describe(f) for f in r.fips[:10]],
                    "n_fips": len(r.fips),
                    "is_national": r.is_national,
                    "severity": r.severity,
                    "novelty": r.novelty,
                    "locality": r.locality_score,
                    "source_urls": r.source_urls[:5],
                    "first_seen": r.first_seen.isoformat(),
                }
                for r in rows
            ],
        }


@router.get("/cards")
def cards(
    status: str | None = None, publishable: bool | None = None, class_id: str | None = None, limit: int = 100
) -> dict:
    """The solution library with verifier outcomes (why a card is or isn't publishable)."""
    from solbrief.db.models import CardStatus, SolutionCard

    with session_scope() as s:
        q = select(SolutionCard)
        if status:
            q = q.where(SolutionCard.status == CardStatus(status))
        if publishable is not None:
            q = q.where(SolutionCard.publishable.is_(publishable))
        if class_id:
            q = q.where(SolutionCard.problem_class_ids.any(class_id))
        rows = s.scalars(q.order_by(SolutionCard.created_at.desc()).limit(min(limit, 500))).all()
        return {
            "count": len(rows),
            "cards": [
                {
                    "id": str(c.id),
                    "slug": c.slug,
                    "title": c.title,
                    "status": c.status.value,
                    "publishable": c.publishable,
                    "grade": c.evidence_grade.value if c.evidence_grade else None,
                    "classes": c.problem_class_ids,
                    "last_verified_at": c.last_verified_at.isoformat() if c.last_verified_at else None,
                    "publishers": (c.verification or {}).get("publishers"),
                    "reasons": (c.verification or {}).get("reasons"),
                    "dropped": [x for x in (c.verification or {}).get("supports", []) if not x.get("ok")],
                }
                for c in rows
            ],
        }
