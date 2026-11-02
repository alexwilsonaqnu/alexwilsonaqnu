"""Operator CLI: `solbrief <command>`. Used by humans via `fly ssh console` and by the scheduler."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date


def _date(value: str) -> date:
    if value == "today":
        from solbrief.alerts import local_today

        return local_today()
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="solbrief")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run-daily", help="run the daily pipeline inline (no queue)")
    run.add_argument("--date", type=_date, default="today")
    run.add_argument("--force", action="store_true")

    enq = sub.add_parser("enqueue-daily", help="enqueue today's daily run (what the scheduler does)")
    enq.add_argument("--force", action="store_true")

    sub.add_parser("check-deadline", help="alert if today's run is late")
    sub.add_parser("worker")
    sub.add_parser("scheduler")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    from solbrief import telemetry

    if args.cmd == "worker":
        from solbrief.worker import main as worker_main

        worker_main()
    elif args.cmd == "scheduler":
        from solbrief.scheduler import main as sched_main

        sched_main()
    elif args.cmd == "run-daily":
        telemetry.configure("cli")
        from solbrief.config import get_settings
        from solbrief.pipeline.daily import run_daily

        out = run_daily(args.date, dry_run=get_settings().effective_dry_run, force=args.force)
        print(
            json.dumps(
                {
                    "date": str(out.run_date),
                    "dry_run": out.dry_run,
                    "status": out.status,
                    "trace_id": out.trace_id,
                }
            )
        )
    elif args.cmd == "enqueue-daily":
        telemetry.configure("cli")
        from solbrief.scheduler import enqueue_daily_run

        print(enqueue_daily_run(force=args.force))
    elif args.cmd == "check-deadline":
        telemetry.configure("cli")
        from solbrief.alerts import check_deadline

        print(check_deadline())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
