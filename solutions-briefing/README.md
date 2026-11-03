# Solutions Briefing

A daily email briefing for US journalists: the problems of the day in their beat and coverage area, each
paired with documented responses — with evidence, limitations, and experts to call. Reporters can also
search the library from the web or by replying to the email. A Knight Lab (Northwestern Medill) project.

**Accuracy over volume.** No claim reaches a reporter unless its supporting quote was found in the cited
source by code, not by a model. See `docs/PLAN.md` §2.

## Status

**Milestone 4: matcher, reporter search, golden-set eval.**
- **Shared matcher** (`matcher/`): a feed problem or a typed problem statement → ranked publishable cards.
  - Hybrid retrieval: taxonomy overlap (with parent/child), pgvector nearest neighbours (Voyage `voyage-3.5`) and
    Postgres full-text search.
  - Interpretable features, all returned with each result: semantic, class, lexical, similar-sized place (Census
    county population bands + same state), evidence grade, verified, recency, and a reporter-feedback penalty.
  - Weights live in `matcher/weights.yaml` and are tuned with `solbrief matcher-fit`.
  - Stale, retracted and unverified cards never match. Service-guidance problems never get solution cards.
- **`match` stage:** each active reader's problems are picked by beat (a parent class covers its children) and
  coverage area (county, state, or national). Up to 3 cards are matched per problem and written to `match_results`.
- **Web search** (FastAPI + Jinja2 + HTMX, served without a CDN):
  - `/` takes a problem statement and an optional state/county; `/search` returns ranked cards with grade badges,
    Provisional labels, the key limitation, verified experts (or SciLine) and "why matched" chips.
  - `/cards/{slug}` shows all four pillars, each with its verified source quote. Unpublished cards return 404.
  - Searches are rate-limited per IP.
- **Golden set:** `evals/matcher_golden/` holds reporter-style problem statements (`proposed` → Knight Lab
  `confirmed`) and a frozen library snapshot. CI reports recall@5 and gates on confirmed pairs.
  *Interim: the snapshot is a fixture library until `solbrief library-build` runs with API keys.*
- **`solbrief library-build`:** drafts up to N cards across classes under a hard total budget
  (`LIBRARY_BUILD_BUDGET_USD`), then verifies and embeds them.

Built on M3 (verifier + seeds + drafter), M2 (ingest) and M1b (platform). The writer, evaluator and delivery
stages are still stubs (M5).

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
  embed.py           Voyage embeddings for cards/problems/queries (skipped without VOYAGE_API_KEY)
  matcher/           shared hybrid matcher, weights.yaml, golden-set tooling (evaluate, fit, snapshot)
  skills/            SKILL.md knowledge packs: four-pillars, evidence-grading, taxonomy, briefing-style
  pipeline/          daily orchestrator + stage registry; ingest.py is stage 1 (each stage idempotent per date)
  queue.py           procrastinate tasks (web enqueues, worker executes)
  scheduler.py       timezone-aware cron (04:30 America/Chicago) — only enqueues
  alerts.py          deadline alert email
  web/               FastAPI: reporter search + card pages (Jinja2/HTMX), /healthz, /admin/*
  email/             Postmark client
tests/, evals/       need Postgres + pgvector; evals/verifier_traps (100% gate), evals/matcher_golden (recall@5)
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
uv run solbrief library-build --cards 40   # draft+verify+embed under LIBRARY_BUILD_BUDGET_USD (needs API keys)
uv run solbrief golden-snapshot            # freeze library + query embeddings for the matcher eval
uv run solbrief matcher-fit                # tune matcher weights on the golden set

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
