---
description: Trigger, monitor, or inspect the daily solutions-journalism briefing run on Fly (the pipeline itself runs in the cloud, never locally)
argument-hint: "[date YYYY-MM-DD, default today] [--env staging|production] [--dry-run] [--stage <name>] [--reader <email>] [--status]"
---

# Operate: Daily Solutions Briefing (on Fly)

The daily pipeline runs on Fly.io: the `scheduler` process starts it every morning, and the `worker` process executes it. This command never runs the pipeline on this machine. It triggers runs on Fly, monitors them, and reports results, using `flyctl` and the app's authenticated admin API.

Arguments: $ARGUMENTS

- Date: default is today (America/Chicago).
- `--env`: `staging` (default) or `production`. Ask me to confirm before triggering any non-dry run on production.
- `--dry-run`: the pipeline runs fully on Fly but sends no email; rendered briefings are stored with the run.
- `--stage <name>`: re-run one stage (`ingest`, `library`, `verify`, `match`, `write`, `evaluate`, `deliver`, `report`).
- `--reader <email>`: limit match/write/evaluate/deliver to one reader.
- `--status`: don't trigger anything; just report the latest run for the date.

## Steps

1. **Check the deployment:** `fly status` for the target app. Confirm `web`, `worker`, and `scheduler` are healthy and the deployed version matches `main`. If anything is unhealthy, stop and report.
2. **Check existing runs:** query the admin API for runs on the date. If a completed run already exists and no `--stage` was passed, report it instead of starting a new one. Stages are idempotent; the deliver stage never resends to a reader who already got today's briefing.
3. **Trigger** (unless `--status`): `POST` to the admin API with the arguments. The admin token comes from the environment, never from a file in the repo.
4. **Monitor:** poll run status until completion or until the run deadline passes. On failure, pull the relevant logs (`fly logs` filtered to the run's trace id) and summarize the cause.
5. **Report:**
   - Problems ingested per source; source failures
   - Cards drafted / verified / promoted / staled / dropped
   - Evaluator rejections by reason
   - Briefings sent / skipped (with reasons), or rendered (dry run)
   - Trace id and a link to the trace

## Pipeline rules

Enforced in the deployed code; restated here so you can check reports against them.

- Never include a fact that isn't traceable to a stored source.
- Verifier and research subagents run in separate contexts. A card is `verified` only with two or more independent sources; a card with no surviving evidence never appears in a briefing.
- The evaluator rejects items missing a pillar or a citation, unlabeled provisional cards, and mixed item types (max 2 revisions, then the item is dropped). If fewer than 2 items survive for a reader, that reader is skipped today.
- Institutional contact paths only for experts.
- If the run fails before Deliver, nothing is sent.

If a report shows any of these rules being broken, treat it as a bug: say so plainly and propose a fix to the pipeline code rather than working around it in this command.
