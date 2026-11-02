"""Run-deadline alerting: email ALERT_EMAIL if today's run isn't complete by RUN_DEADLINE (America/Chicago).

The alert is recorded as a `runs` row with stage='deadline_alert' using INSERT ... ON CONFLICT DO NOTHING,
so it fires at most once per day even though the check runs every 10 minutes.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert

from solbrief.config import get_settings
from solbrief.db import session_scope
from solbrief.db.models import Run, RunStatus
from solbrief.email.postmark import send_email
from solbrief.pipeline.daily import is_complete

log = logging.getLogger(__name__)


def local_today(now: datetime | None = None) -> date:
    s = get_settings()
    now = now or datetime.now(UTC)
    return now.astimezone(ZoneInfo(s.schedule_timezone)).date()


def check_deadline(now: datetime | None = None) -> str:
    """Returns 'not_due' | 'ok' | 'alerted' | 'already_alerted'."""
    s = get_settings()
    tz = ZoneInfo(s.schedule_timezone)
    now = now or datetime.now(UTC)
    local = now.astimezone(tz)
    if local.time() < s.run_deadline:
        return "not_due"
    today = local.date()
    dry_run = s.effective_dry_run
    if is_complete(today, dry_run):
        return "ok"

    with session_scope() as sess:
        inserted = sess.execute(
            insert(Run)
            .values(
                run_date=today,
                stage="deadline_alert",
                dry_run=dry_run,
                status=RunStatus.failed,
                counts={},
                errors=[{"msg": f"run not complete by {s.run_deadline}"}],
                started_at=now,
                finished_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_run_day_stage")
            .returning(Run.id)
        ).scalar()
    if inserted is None:
        return "already_alerted"

    subject = f"[solbrief {s.app_env}] daily run for {today} not complete by {s.run_deadline:%H:%M} {tz.key}"
    body = (
        f"The {'dry-run ' if dry_run else ''}daily run for {today} had not completed by the deadline.\n"
        f"Check GET /admin/runs/{today} and the Logfire trace for details."
    )
    if s.alert_email:
        send_email(s.alert_email, subject, body)
    else:
        log.error("ALERT_EMAIL not set — %s", subject)
    return "alerted"
