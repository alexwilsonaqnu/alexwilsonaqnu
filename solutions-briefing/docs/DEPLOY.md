# Deploying to Fly.io — first-time setup (Milestone 1b)

Everything runs on Fly. Your machine only runs `flyctl`. Run these once, in order.
Commands assume the Fly org is `personal`; change `--org` if Knight Lab has its own org.

## 0. Names

Fly app names are global. The configs use `solbrief` (prod) and `solbrief-staging`. If either is taken,
pick new names and update `app =` in `fly.toml` / `fly.staging.toml` and the two smoke-test URLs in
`.github/workflows/deploy.yml`.

## 1. Staging app + database

```sh
fly apps create solbrief-staging --org personal

# Managed Postgres (Basic plan, PG 17). Note the cluster id it prints.
fly mpg create --name solbrief-staging-db --region ord --plan basic --pg-major-version 17 --org personal
# If MPG isn't offered in ord, use iad here AND set primary_region = "iad" in fly.staging.toml.

# Attach: sets DATABASE_URL as a secret on the app.
fly mpg attach <cluster-id> --app solbrief-staging
```

pgvector and citext are created by the first migration (`CREATE EXTENSION IF NOT EXISTS`). If MPG
requires extensions to be enabled from the dashboard first, enable `vector` and `citext` there.
(Flag names on `fly mpg create` change between flyctl releases — check `fly mpg create --help`.)

## 2. Staging secrets

```sh
fly secrets set --app solbrief-staging \
  ADMIN_TOKEN="$(openssl rand -hex 32)" \
  HMAC_SECRET="$(openssl rand -hex 32)" \
  ALERT_EMAIL="you@northwestern.edu" \
  LOGFIRE_TOKEN="<logfire write token for the staging project>" \
  POSTMARK_SERVER_TOKEN="<postmark server token>" \
  POSTMARK_FROM="briefing@<your verified sender domain>" \
  ANTHROPIC_API_KEY="<key>" \
  VOYAGE_API_KEY="<key>"
```

Keep a copy of `ADMIN_TOKEN` in your password manager — it's how you call the admin API.
Secrets are never committed; `.env.example` lists every name.

## 3. First deploy + scale

```sh
fly deploy --config fly.staging.toml           # release_command runs `alembic upgrade head` first
fly scale count web=1 worker=1 scheduler=1 --app solbrief-staging
```

**The scheduler must stay at exactly 1 machine.** (A second copy would be harmless — the queue and the
pipeline lock deduplicate — but it is wasteful and noisy.)

## 4. Verify (the M1b demo)

```sh
curl https://solbrief-staging.fly.dev/healthz
# {"ok":true,"db":"ok","schema_revision":"0001","env":"staging"}

curl -X POST https://solbrief-staging.fly.dev/admin/runs \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'content-type: application/json' -d '{}'
# {"job_id":1,"date":"...","dry_run":true}

curl https://solbrief-staging.fly.dev/admin/runs/$(TZ=America/Chicago date +%F) \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# 8 stages, one trace_id — open that trace in Logfire.

fly logs --app solbrief-staging     # scheduler logs "scheduled daily_run: cron[hour='4', minute='30']"
```

## 5. CI/CD

```sh
fly tokens create deploy --app solbrief-staging -x 999999h   # copy the whole "FlyV1 ..." string
```

In GitHub → repo → Settings:
* **Secrets → Actions:** `FLY_API_TOKEN_STAGING` = that token.
* **Environments:** create `staging` and `production` (add yourself as a required reviewer on `production`).

From then on: every PR runs CI (lint, migrations up/down/up + drift check, tests, image build, evals);
every merge to `main` deploys staging and smoke-tests it.

## 6. Production (Milestone 7, not before)

Repeat steps 1–3 with `solbrief` / `solbrief-db` / `fly.toml`, add `FLY_API_TOKEN_PRODUCTION`, then set
the repository variable `DEPLOY_PRODUCTION=true`. Production keeps `DRY_RUN=true` until you flip it.

## Postmark setup (needed by M5, alerts work as soon as the token is set)

1. Create a Server; verify the sender domain (DKIM + Return-Path DNS records).
2. Streams: the default **Transactional** stream (`outbound`) for alerts and reply-to-query answers,
   and a **Broadcast** stream named `broadcast` for daily briefings.
3. Inbound (M6): set the inbound webhook URL to `https://<app>.fly.dev/webhooks/postmark/inbound`
   with HTTP basic auth (the credentials go in `POSTMARK_INBOUND_WEBHOOK_SECRET`).

## Logfire setup

Create two projects (`solbrief-staging`, `solbrief`) and a **write token** for each; set it as `LOGFIRE_TOKEN`.
Each pipeline run is one trace named `daily_run <date>`; its id is stored in `runs.trace_id`.
