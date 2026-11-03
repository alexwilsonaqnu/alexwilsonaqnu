# Solutions Briefing

A daily email briefing for US journalists: the problems of the day in their beat and coverage area, each
paired with documented responses — with evidence, limitations, and experts to call. Reporters can also
search the library from the web or by replying to the email. A Knight Lab (Northwestern Medill) project.

**Accuracy over volume.** No claim reaches a reporter unless its supporting quote was found in the cited
source by code, not by a model. See `docs/PLAN.md` §2.

## Status

**Milestone 3: solution library and verifier.**
- **Seeds:** 115 legitimate seed sources, all federal: 15 hand-curated CDC Community Guide findings, plus
  What Works Clearinghouse interventions generated from the official bulk export.
- **Drafter:** a Claude Agent SDK agent in an isolated session with no built-in tools. It has only two tools,
  `fetch_source` and `search_openalex`, and returns a card draft that must match the `CardDraft` schema.
  Drafts are stored *unverified*.
- **Verifier:** deterministic code with no model judgment.
  - Every quote must be found in its fetched source, and numbers must match exactly.
  - A claim can't introduce a number its quote lacks, or flip the quote's polarity.
  - Blocked sources never verify.
  - The response and insight pillars carry their own quotes, and numbers in the card's own prose must be quoted.
  - A card is *verified* only with two independent publishers; otherwise it stays *provisional* (always labelled).
  - Re-verification marks a card *stale* when sources disappear or experts move.
  - An optional Claude entailment check can only drop a claim, never rescue one.
- **Eval gate:** `evals/verifier_traps/` holds planted unsupported claims against recorded real sources. CI fails
  unless 100% are caught and no genuine quote is rejected.

Built on M2 (problem ingest from NWS, OpenFEMA and CDC) and M1b (web, worker, scheduler, schema, queue, Logfire,
CI/CD). The matcher, writer, evaluator and delivery stages are still stubs that report `skipped` (M4–M5).

## Layout

```
src/solbrief/
  config.py          settings from env (Fly secrets)
  db/                SQLAlchemy models + Alembic migrations (schema: docs/PLAN.md §3)
  adapters/          problem sources: nws, openfema, cdc_rss (common SourceAdapter + polite HTTP)
  geo/               FIPS states/counties (Census gazetteer) and deterministic place tagging
  taxonomy/          frozen v1.yaml (seeded by migration 0002); changes via taxonomy_changes only
  classify.py        rules first, optional Claude fallback (structured output limited to taxonomy ids)
  library/           seeds (seeds_cg.yaml curated, seeds_wwc.yaml generated), CardDraft contract + storage
  agents/drafter.py  Agent SDK drafter (isolated session, two narrow tools, structured output)
  verify/            fetch+extract, span/claim checks, publishers & grade caps, experts, card verifier
  skills/            SKILL.md knowledge packs: four-pillars, evidence-grading, taxonomy, briefing-style
  pipeline/          daily orchestrator + stage registry; ingest.py is stage 1 (each stage idempotent per date)
  queue.py           procrastinate tasks (web enqueues, worker executes)
  scheduler.py       timezone-aware cron (04:30 America/Chicago) — only enqueues
  alerts.py          deadline alert email
  web/               FastAPI: /healthz, /admin/*
  email/             Postmark client
tests/, evals/       need Postgres + pgvector; evals/verifier_traps is the 100% verifier gate
docs/PLAN.md         the plan (Milestone 1)
docs/DEPLOY.md       Fly.io setup runbook
```

## Local development

```sh
uv sync --extra dev
cp .env.example .env            # point DATABASE_URL at a Postgres with pgvector
uv run alembic upgrade head
uv run uvicorn solbrief.web.app:app --reload
uv run python -m solbrief.worker
uv run solbrief run-daily --date today     # run the pipeline inline (dry run)
uv run solbrief library-draft --class homelessness --limit 1   # draft from seeds (needs ANTHROPIC_API_KEY)
uv run solbrief library-verify             # verify new/due cards now
uv run solbrief seeds-build-wwc export.zip # regenerate WWC seeds from the official export

# tests (creates/drops a database named solbrief_test on this server)
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest
```

A Postgres with pgvector: `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg17`.

## Admin API

All routes need `Authorization: Bearer $ADMIN_TOKEN`.

| Route | Does |
|---|---|
| `POST /admin/runs` `{"date"?, "dry_run"?, "force"?}` | enqueue a daily run (staging is always dry-run) |
| `GET /admin/runs/{date}` | per-stage status, counts, errors, trace id |
| `POST /admin/runs/{date}/stages/{stage}/rerun` | re-run one stage |
| `GET /admin/problems/{date}?class_id=&fips=&limit=` | the day's problem feed, ranked by severity then locality |
| `GET /admin/cards?status=&publishable=&class_id=` | the solution library with verifier outcomes and drop reasons |
