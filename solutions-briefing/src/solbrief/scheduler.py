"""Scheduler process (Fly process group `scheduler`, exactly one machine).

Fly's native scheduled machines can't fire at an exact time of day, so this process runs a
timezone-aware cron (APScheduler) whose only job is to ENQUEUE work. All work happens on the worker.
A double-fire is harmless: procrastinate's queueing lock rejects a duplicate queued job and the
pipeline's advisory lock + completion check make a duplicate execution a no-op.
"""

from __future__ import annotations

import logging

import logfire
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from solbrief import telemetry
from solbrief.alerts import check_deadline, local_today
from solbrief.config import get_settings

log = logging.getLogger(__name__)


def enqueue_daily_run(force: bool = False) -> str:
    from procrastinate.exceptions import AlreadyEnqueued

    from solbrief.queue import app, daily_run_lock, daily_run_task

    s = get_settings()
    run_date = local_today()
    dry_run = s.effective_dry_run
    with logfire.span("enqueue daily_run {run_date}", run_date=str(run_date), dry_run=dry_run):
        with app.open():
            try:
                daily_run_task.configure(queueing_lock=daily_run_lock(run_date, dry_run)).defer(
                    run_date=run_date.isoformat(), dry_run=dry_run, force=force
                )
            except AlreadyEnqueued:
                return "already_enqueued"
    return "enqueued"


def build_scheduler() -> BlockingScheduler:
    s = get_settings()
    sched = BlockingScheduler(timezone=s.schedule_timezone)
    sched.add_job(
        enqueue_daily_run,
        CronTrigger(hour=s.daily_run_time.hour, minute=s.daily_run_time.minute, timezone=s.schedule_timezone),
        id="daily_run",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        check_deadline,
        CronTrigger(minute="*/10", timezone=s.schedule_timezone),
        id="check_deadline",
        coalesce=True,
        max_instances=1,
    )
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    telemetry.configure("scheduler")
    sched = build_scheduler()
    for job in sched.get_jobs():
        log.info("scheduled %s: %s", job.id, job.trigger)
    sched.start()


if __name__ == "__main__":
    main()
