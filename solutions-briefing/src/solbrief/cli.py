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

    wwc = sub.add_parser("seeds-build-wwc", help="regenerate WWC seeds from the official bulk export (zip/csv)")
    wwc.add_argument("export")
    wwc.add_argument("--out", default="src/solbrief/library/seeds_wwc.yaml")

    draft = sub.add_parser("library-draft", help="draft cards from unused seeds for a class (needs ANTHROPIC_API_KEY)")
    draft.add_argument("--class", dest="class_id", required=True)
    draft.add_argument("--limit", type=int, default=1)

    sub.add_parser("library-verify", help="run the verifier on new and due cards now")

    build = sub.add_parser(
        "library-build", help="draft N cards across classes under LIBRARY_BUILD_BUDGET_USD, verify, embed"
    )
    build.add_argument("--cards", type=int, default=40)

    sub.add_parser("matcher-fit", help="fit matcher weights on the golden set; rewrites matcher/weights.yaml")
    sub.add_parser("golden-snapshot", help="freeze publishable cards + query embeddings into evals/matcher_golden")
    sub.add_parser("embed", help="embed publishable cards missing a current embedding")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    from solbrief import telemetry

    if args.cmd == "seeds-build-wwc":
        from pathlib import Path

        import yaml

        from solbrief.library.seeds import build_wwc_seeds

        doc = build_wwc_seeds(Path(args.export))
        header = (
            "# GENERATED from the What Works Clearinghouse bulk export (federal data) by `solbrief seeds-build-wwc`.\n"
            "# Do not edit by hand; re-generate. Ratings are WWC effectiveness ratings per outcome domain.\n"
        )
        Path(args.out).write_text(header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120))
        print(f"{len(doc['seeds'])} WWC seeds written to {args.out}")
        return 0
    if args.cmd == "library-draft":
        telemetry.configure("cli")
        from solbrief.config import get_settings
        from solbrief.pipeline.library_stage import draft_from_seeds

        if not get_settings().anthropic_api_key:
            print("ANTHROPIC_API_KEY is not set; the drafter needs it.")
            return 2
        print(json.dumps(dict(draft_from_seeds(args.class_id, args.limit))))
        return 0
    if args.cmd == "library-verify":
        telemetry.configure("cli")
        from solbrief.alerts import local_today
        from solbrief.pipeline.stages import StageContext
        from solbrief.pipeline.verify_stage import verify

        result = verify(StageContext(run_date=local_today(), dry_run=True))
        print(json.dumps({"status": result.status.value, **result.counts}, default=str))
        return 0

    if args.cmd == "library-build":
        telemetry.configure("cli")
        from solbrief.alerts import local_today
        from solbrief.config import get_settings
        from solbrief.library.build import build_library
        from solbrief.pipeline.stages import StageContext
        from solbrief.pipeline.verify_stage import verify

        if not get_settings().anthropic_api_key:
            print("ANTHROPIC_API_KEY is not set; the drafter needs it.")
            return 2
        report = build_library(args.cards)
        print(
            json.dumps(
                {
                    "budget_usd": report.budget_usd,
                    "spent_usd": report.spent_usd,
                    "attempted": report.attempted,
                    "drafted": report.drafted,
                    "failures": dict(report.failures),
                    "by_class": dict(report.by_class),
                    "stopped_because": report.stopped_because,
                },
                indent=1,
            )
        )
        result = verify(StageContext(run_date=local_today(), dry_run=True))
        print(json.dumps({"verify": result.status.value, **result.counts}, default=str, indent=1))
        return 0
    if args.cmd == "embed":
        from solbrief.embed import embed_pending

        print(json.dumps(embed_pending()))
        return 0
    if args.cmd in ("matcher-fit", "golden-snapshot"):
        from pathlib import Path

        import yaml

        from solbrief.config import get_settings
        from solbrief.db import session_scope
        from solbrief.embed import voyage_embedder
        from solbrief.matcher import default_weights
        from solbrief.matcher.golden import (
            GOLDEN_DIR,
            build_snapshot,
            evaluate,
            fit_weights,
            load_pairs,
            load_snapshot,
            snapshot_embedder,
        )

        doc, pairs = load_pairs()
        if args.cmd == "golden-snapshot":
            with session_scope() as s:
                snap = build_snapshot(s, pairs, voyage_embedder(), get_settings().voyage_model)
            (GOLDEN_DIR / "library.json").write_text(json.dumps(snap) + "\n")
            print(f"snapshot: {len(snap['cards'])} cards, {len(snap['query_embeddings'])} query embeddings")
            return 0
        # matcher-fit runs against the frozen snapshot in a throwaway transaction (rolled back).
        with session_scope() as s:
            snap = load_snapshot(s)
            emb = snapshot_embedder(snap, pairs)
            weights, before, after = fit_weights(s, pairs, default_weights(), emb)
            per_pair = evaluate(s, pairs, emb, weights).per_pair
            s.rollback()
        path = Path(__file__).parent / "matcher" / "weights.yaml"
        wdoc = yaml.safe_load(path.read_text())
        wdoc.update(
            version=wdoc.get("version", 1) + 1,
            fitted_on=f"{len(pairs)} golden pairs ({snap.get('kind')})",
            recall_at_5=after,
            weights=weights,
        )
        path.write_text(
            "# Fitted by `solbrief matcher-fit`. score = Σ weight × feature.\n" + yaml.safe_dump(wdoc, sort_keys=False)
        )
        print(json.dumps({"recall_at_5_before": before, "recall_at_5_after": after, "weights": weights}, indent=1))
        for row in per_pair:
            if row["hit"] is not None and row["hit"] < 1:
                print("  miss", json.dumps(row))
        return 0

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
