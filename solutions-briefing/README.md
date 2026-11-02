# Solutions Briefing

A daily email briefing for US journalists: the problems of the day in their beat and coverage area, each
paired with documented responses — with evidence, limitations, and experts to call. Reporters can also
search the library from the web or by replying to the email. A Knight Lab (Northwestern Medill) project.

**Accuracy over volume.** No claim reaches a reporter unless its supporting quote was found in the cited
source by code, not by a model. See `docs/PLAN.md` §2.

## Status

Milestone 1b — deployable skeleton. Web (health + admin API), worker, scheduler, full schema with
migrations, Postgres job queue, OpenTelemetry tracing (Logfire), CI/CD. Pipeline stages are registered
stubs that report `skipped`; they are filled in M2–M5.

## Layout

```
src/solbrief/
  config.py          settings from env (Fly secrets)
  db/                SQLAlchemy models + Alembic migrations (schema: docs/PLAN.md §3)
  pipeline/          daily orchestrator + stage registry (each stage idempotent per date)
  queue.py           procrastinate tasks (web enqueues, worker executes)
  scheduler.py       timezone-aware cron (04:30 America/Chicago) — only enqueues
  alerts.py          deadline alert email
  web/               FastAPI: /healthz, /admin/*
  email/             Postmark client
tests/               needs Postgres + pgvector
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
