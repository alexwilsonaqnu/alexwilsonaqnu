from datetime import UTC, date, datetime, time

from sqlalchemy import text

from solbrief.alerts import check_deadline, local_today
from solbrief.db import get_engine
from solbrief.pipeline.daily import run_daily
from solbrief.scheduler import build_scheduler, enqueue_daily_run

# 2026-11-02 is after the US DST change (Nov 1), so Chicago is UTC-6.
BEFORE_DEADLINE = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)  # 06:00 CST
AFTER_DEADLINE = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)  # 07:00 CST


def test_daily_job_fires_at_0430_chicago():
    sched = build_scheduler()
    job = sched.get_job("daily_run")
    fire = job.trigger.get_next_fire_time(None, datetime(2026, 11, 2, 0, 0, tzinfo=UTC))
    assert fire.astimezone(UTC) == datetime(2026, 11, 2, 10, 30, tzinfo=UTC)  # 04:30 CST
    # and across DST (July: CDT, UTC-5)
    fire_summer = job.trigger.get_next_fire_time(None, datetime(2026, 7, 1, 0, 0, tzinfo=UTC))
    assert fire_summer.astimezone(UTC) == datetime(2026, 7, 1, 9, 30, tzinfo=UTC)


def test_local_today_uses_chicago():
    # 03:00 UTC on Nov 3 is still Nov 2 in Chicago
    assert local_today(datetime(2026, 11, 3, 3, 0, tzinfo=UTC)) == date(2026, 11, 2)


def test_enqueue_is_deduplicated():
    assert enqueue_daily_run() == "enqueued"
    assert enqueue_daily_run() == "already_enqueued"
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM procrastinate_jobs")).scalar() == 1


def test_deadline_not_due_before_deadline():
    assert check_deadline(BEFORE_DEADLINE) == "not_due"


def test_deadline_alerts_once_when_run_missing():
    assert check_deadline(AFTER_DEADLINE) == "alerted"
    assert check_deadline(AFTER_DEADLINE) == "already_alerted"


def test_deadline_ok_when_run_complete():
    run_daily(date(2026, 11, 2), dry_run=True)
    assert check_deadline(AFTER_DEADLINE) == "ok"


def test_settings_defaults():
    from solbrief.config import get_settings

    s = get_settings()
    assert s.daily_run_time == time(4, 30) and s.run_deadline == time(6, 30)
