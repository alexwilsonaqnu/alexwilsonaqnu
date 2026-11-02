from datetime import date

from sqlalchemy import select

from solbrief.db import session_scope
from solbrief.db.models import Run, RunStatus
from solbrief.pipeline import stages
from solbrief.pipeline.daily import run_daily
from solbrief.pipeline.stages import STAGE_ORDER

D = date(2026, 11, 2)


def _rows(dry_run=True):
    with session_scope() as s:
        return {r.stage: r for r in s.scalars(select(Run).where(Run.run_date == D, Run.dry_run == dry_run))}


def test_daily_run_records_every_stage_with_one_trace():
    out = run_daily(D, dry_run=True)
    assert out.status == "completed"
    rows = _rows()
    assert set(rows) == set(STAGE_ORDER)
    trace_ids = {r.trace_id for r in rows.values()}
    assert len(trace_ids) == 1 and None not in trace_ids, "one trace per run"
    assert rows["run_report"].status == RunStatus.succeeded
    assert rows["ingest"].status == RunStatus.skipped  # stub until M2


def test_completed_run_is_not_repeated_unless_forced():
    run_daily(D, dry_run=True)
    assert run_daily(D, dry_run=True).status == "already_done"
    forced = run_daily(D, dry_run=True, force=True)
    assert forced.status == "completed"
    assert _rows()["ingest"].attempt == 2


def test_failing_stage_makes_run_partial_and_is_recorded(monkeypatch):
    def boom(ctx):
        raise RuntimeError("source down")

    monkeypatch.setitem(stages._REGISTRY, "ingest", boom)
    out = run_daily(D, dry_run=True)
    assert out.status == "partial"
    rows = _rows()
    assert rows["ingest"].status == RunStatus.failed
    assert rows["ingest"].errors[0]["msg"] == "source down"
    assert rows["run_report"].counts["failed_stages"] == ["ingest"]
    # later stages still ran
    assert rows["deliver"].status == RunStatus.skipped


def test_dry_run_and_live_runs_are_tracked_separately():
    run_daily(D, dry_run=True)
    assert _rows(dry_run=False) == {}
