"""End to end: scheduler enqueues -> worker executes -> runs table shows a completed traced run."""

from sqlalchemy import text

from solbrief.alerts import local_today
from solbrief.db import get_engine
from solbrief.pipeline.daily import is_complete
from solbrief.queue import app
from solbrief.scheduler import enqueue_daily_run


def test_worker_executes_enqueued_daily_run():
    assert enqueue_daily_run() == "enqueued"
    # wait=False: process everything currently queued, then return.
    app.run_worker(queues=["pipeline"], wait=False, install_signal_handlers=False)
    assert is_complete(local_today(), dry_run=True)
    with get_engine().connect() as conn:
        status = conn.execute(text("SELECT status FROM procrastinate_jobs WHERE task_name='daily_run'")).scalar()
    assert status == "succeeded"
