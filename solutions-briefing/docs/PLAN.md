# Solutions Journalism Briefing System: Milestone 1 Plan

Knight Lab / Northwestern Medill · drafted 2026-11-01 · status: **approved 2026-11-01; Milestone 1b built**

This document covers Milestone 1: the implementation plan, the source access report, the schema, and the Fly
deployment plan. The Milestone 1b skeleton implements §3 and §5; `docs/DEPLOY.md` is the Fly runbook.

---

## 0. Decisions (resolved 2026-11-01)

| # | Decision |
|---|---|
| Q1 | **New repo** for this project (this one). |
| Q3 | **Postmark** (Pro): Transactional stream for alerts and replies, Broadcast stream for daily briefings, inbound webhook with `StrippedTextReply`. |
| Q4 | **Pydantic Logfire** for OTel traces. |
| Q5 | **Voyage AI `voyage-3.5`** embeddings (1024-dim). |
| Q2, Q6–Q12 | Recommendations accepted: SJN partnership request (federal clearinghouses in the meantime); separate staging MPG cluster; Knight Lab curates the golden set and registers API keys; run 04:30 / deadline 06:30 America/Chicago; pilot of 20–50 reporters; hybrid Agent SDK + Messages API; FastAPI + Jinja2 + HTMX. |

**Changes made while building M1b:**
* **Scheduler is APScheduler, not supercronic.** The container couldn't fetch and checksum the supercronic binary,
  and a pure-Python, timezone-aware cron in the `scheduler` process group is equivalent: it only enqueues, and
  tests cover its DST behavior. It runs as `python -m solbrief.scheduler`.
* Telemetry is exported with `LOGFIRE_TOKEN` rather than generic `OTEL_EXPORTER_OTLP_*` variables. The spans are standard OTel.
* Base image comes from the AWS ECR mirror of Docker Official Images, because Docker Hub rate-limits anonymous pulls.

<details><summary>Original open questions (for the record)</summary>

| # | Question | My recommendation |
|---|---|---|
| Q1 | **Which repo?** This repository holds the Anaplan FP&A project. Its hooks (`no-math-gate`) already block ordinary journalism work: an ISSN and a heredoc were both flagged as finance arithmetic. Its CLAUDE.md describes a different product. | Create a new repo (e.g. `solutions-briefing`), move this plan there, and delete `solutions-briefing/` from this repo. |
| Q2 | **SJN Story Tracker is RED.** Its terms forbid storing content without written permission, and robots.txt blocks ClaudeBot. It was meant to supply the seed corpus, the taxonomy seed and the golden set. | Knight Lab requests a data partnership from SJN now (exact ask in §1.2). Until then, M3 seeds from What Works Clearinghouse (WWC) and the CDC Community Guide (both GREEN, federal). The taxonomy is our own and mapped to SJN tags only after permission. |
| Q3 | Email: **Postmark or Resend?** | **Postmark Pro.** Its inbound webhook includes `StrippedTextReply`, and its Broadcast stream adds RFC 8058 one-click unsubscribe automatically. Resend's inbound webhook carries metadata only, so we would have to fetch and parse replies ourselves. |
| Q4 | OTel backend? | **Pydantic Logfire** (free tier of 10M records/mo, OTel-native, Python-first). Alternative: **Langfuse** if you want views of LLM prompts, cost and evals. |
| Q5 | Embeddings vendor (not named in the brief) | **Voyage AI `voyage-3.5`** (Anthropic's recommended partner; a second vendor). Fallback: a local `bge-small` model via fastembed. |
| Q6 | Staging database: a separate MPG cluster, or a second database on the prod cluster? | Separate cluster, so staging never competes with production for resources. The cheaper option is a second database on the same cluster. |
| Q7 | Who curates the golden set and the verifier trap set? (They were to come from Story Tracker.) | Knight Lab students or editors confirm about 30 problem → card pairs drawn from WWC, the Community Guide and J-PAL. I build the tooling and draft the candidates. |
| Q8 | API keys that must be tied to a real person: OpenAlex (now paid-credit, with a free daily allowance per key), BLS, Census (key now mandatory), EIA, Semantic Scholar | A Knight Lab staff member registers them under an institutional email. I won't register them myself. |
| Q9 | Daily run time, completion deadline and send time | Run 04:30 America/Chicago, deadline 06:30, send 06:45. Per-reader timezones can come later. |
| Q10 | Pilot list size and launch geography | 20–50 reporters, national plus 2–3 metros, so local ranking can be checked by hand. |
| Q11 | Agent SDK for every LLM call, or a hybrid? | Hybrid (§2.4). |
| Q12 | Web framework | **FastAPI + Jinja2 + HTMX**: server-rendered with no JS build; one process serves the UI, webhook and admin API. |

</details>

---

## 1. Source access report

All checks were run from the cloud network on 2026-11-01. Several sites block datacenter IPs at their CDN: CDC pages, BLS, the
FEMA terms page, Eviction Lab, Cochrane, Bloomberg Cities and SAGE/Wiley. Their terms are therefore **UNVERIFIED** and must be
re-read from a campus network. I did not work around any block, and I will not build adapters that do.

### 1.1 Problem sources

| Source | Access | Auth / limits | County FIPS? | Verdict | Notes |
|---|---|---|---|---|---|
| NWS alerts (api.weather.gov) | REST GeoJSON | User-Agent **required** (an empty one gets 403); limits not published, retry ~5 s after an error | **Yes**: `geocode.SAME` = `0` + state + county FIPS | GREEN | Public domain. Feeds service guidance and problems. |
| OpenFEMA DisasterDeclarationsSummaries v2 | REST (OData) | none; limits UNVERIFIED | **Yes**: `fipsStateCode` + `fipsCountyCode` | GREEN | Refreshed about daily. |
| CDC MMWR | RSS `tools.cdc.gov/api/v2/resources/media/342778.rss` | none | No; needs text geotagging | GREEN | Live. |
| CDC US Outbreaks | RSS `tools.cdc.gov/api/v2/resources/media/285676.rss` | none | No | GREEN | Live. |
| **CDC HAN** | The only feed found is dead (last dated 2020, empty); the HTML pages are blocked to us | — | — | **RED** | No adapter. Ask CDC for a feed, or re-check from campus. |
| GDELT Events/GKG 2.0 | 15-minute bulk CSV | none | Partial: ADM2 codes need a crosswalk to FIPS; noisy | GREEN | Must cite GDELT with a link. |
| GDELT DOC 2.0 API | REST | 1 request per 5 s per IP (429s seen) | No | AMBER | Low-volume keyword timelines and prior-coverage lookup only. |
| News RSS | RSS | per publisher | No | AMBER/RED | NYT RSS is personal, non-commercial use only. AP and Reuters have no public RSS. **Headline + link only**, from an allowlist of publishers whose terms have been reviewed. |
| State health depts | ~50 heterogeneous feeds, no aggregator | varies | sometimes | RED as an aggregate | Hand-checked list of states with real feeds; the rest are flagged as uncovered. |
| BLS API v2 | REST | key needed in practice | Yes (LAUS/QCEW) | GREEN with key | Release-calendar triggers. |
| Census API | REST | **key now mandatory** | Yes | GREEN with key | Also supplies demographics for the matcher. |
| EIA API v2 | REST | key required | mostly state | GREEN with key | |
| Eviction Lab ETS | download (UNVERIFIED) | UNVERIFIED | partial | AMBER | Site blocked to us. Email the lab about terms and redistribution before building an adapter. |

### 1.2 Solution sources

| Source | Access | Verdict | Action |
|---|---|---|---|
| **SJN Story Tracker** | HTML only, no API or export. robots blocks ClaudeBot and GPTBot (crawl-delay 10). ToS: "Copying or storing of the Content for other than personal use is expressly prohibited without prior written permission." | **RED** | **Partnership request:** a metadata export or API (headline, outlet, URL, issue areas, location, date); permission to use the issue-area taxonomy (24 top-level areas, about 489 nodes in a 3-level tree); permission to quote short spans with attribution in a nonprofit email. |
| What Works Clearinghouse (IES) | Official bulk zip plus data dictionary | GREEN | M3 seed. |
| CDC Community Guide | HTML findings pages, federal | GREEN | M3 seed; fetched politely and cached. |
| Campbell Systematic Reviews | Crossref metadata; now published by SAGE | GREEN/AMBER | Check the license on each item: CC BY / CC BY-NC are OK; Wiley-only items get a link only. |
| **Cochrane** | ToS §5.2 forbids text and data mining and bots | **RED** | Link by DOI only; request a TDM license. |
| J-PAL | HTML + RSS; license UNVERIFIED | AMBER | Ask permission. |
| Results for America | HTML, all rights reserved | AMBER | Ask permission. |
| Bloomberg Cities (JHU) | blocked to us | AMBER/RED | Ask whether a feed exists. |
| Harvard Ash Center | WP REST + RSS. The Innovations Awards have ended; the archive is on Archive-It only. | AMBER | Headlines and RSS OK; ask before reusing the archive. |
| OpenAlex | REST + free S3 snapshot; **key now needed** | GREEN | CC0. Snapshot for bulk, API for lookups. |
| Semantic Scholar | REST, keyed rate of 1 request per second; no redistribution | AMBER | Internal enrichment only, with attribution. |

### 1.3 Expert sources

* **OpenAlex/S2 authors.** Affiliation is derived from recent works, so it lags job moves, and few profiles carry an ORCID.
  We display "last confirmed affiliation (date)" and re-confirm against an institutional page before marking an expert verified.
* **University media-relations offices.** The office URL is stored as `contact_path`. The aggregators (Newswise, ExpertFile, ProfNet) are commercial and need a partnership.
* **SciLine (AAAS).** A free expert-matching referral for reporters, linked from every card (`sciline.org/services/expert-matching/`). Never scraped.
* **Hard rule:** no personal emails or phones are ever stored. Enforced by a DB CHECK constraint and a validator.

---

## 2. Architecture

```
                ┌──────────── worker (Agent SDK orchestrator) ─────────────┐
 sources ──►  ingest ─► problems ──┐                                         │
 (adapters)                         ├─► matcher ─► writer ⇄ evaluator ─► deliver ─► run report
 clearing- ─► library research ─► verifier ─► solution_cards (verified only)  │
 houses        (drafter ctx)        (separate ctx, deterministic fetch)       │
                └──────────────────────────────────────────────────────────┘
 web (Starlette/FastAPI): search ─► same matcher ─► writer ⇄ evaluator (mini-briefing)
 inbound email webhook ─► queue ─► worker ─► reply
 scheduler (APScheduler) ─► enqueue daily_run(date)
```

**Key design choice: deterministic code where possible, LLM only where judgment is needed.**
Every stage is a Python function with typed I/O (Pydantic). The LLM subagents are confined to:
classification (problem → class), drafting cards, claim/span alignment assistance, writing, and evaluating.
Everything that can be checked mechanically is checked mechanically:

| Check | Mechanism (not LLM) |
|---|---|
| Quoted support span appears in source | fetch → normalize text → fuzzy substring match (rapidfuzz, threshold ≥ 0.92 on normalized whitespace/quotes) |
| Every factual sentence has a citation | writer emits structured JSON (`sentence`, `citation_ids[]`); renderer refuses sentences with no citation unless typed `framing` |
| Citations point to real library/problem records | citation ids validated against DB rows passed into the writer context |
| Provisional label shown | renderer injects label from `status`; writer cannot omit it |
| Item types never mixed | separate sections rendered from `item_type`; evaluator double-checks |
| Expert contact = institutional | URL domain allowlist (`.edu`, `.gov`, `.org` institutional pages, known media-relations paths); regex rejects emails/phones in stored fields |
| ≥2 independent sources for `verified` | distinct registrable domains AND distinct publishers |

The LLM evaluator then judges what code can't (does the "insight" actually explain a mechanism? is the
story angle supported by the card?). This is the trust model: **a model can drop or reword, never add an
unverified fact**, because the renderer only outputs text tied to verified rows.

### 2.1 Two stores
* **Problem feed** — `problems`, rewritten daily, clustered, scored.
* **Solution library** — `solution_cards`, evergreen, indexed by problem class.

### 2.2 Matcher (shared)
`match(problem_text | problem_id, geo?, k) -> RankedCards`
1. Classify input → problem classes (cached LLM call; for feed items classes already exist).
2. Candidate set: cards with overlapping `problem_class_ids` ∪ top-50 pgvector ANN on embedding.
3. Score = w1·cosine + w2·class overlap (Jaccard, with parent-class partial credit) + w3·geo/demographic
   similarity (population band of card's `where` FIPS vs problem's FIPS, urban/rural RUCC code, region)
   + w4·evidence grade + w5·recency of verification − penalty(already_covered / bad_lead feedback).
4. Weights fit on the golden set (recall@5), stored in config, versioned.
5. Only `verified` cards by default; `provisional` cards allowed but always labeled; `stale`/`retracted` never.

Embeddings: **Voyage AI `voyage-3.5`** (Anthropic's recommended embedding partner) — *needs your OK, it is a
second vendor*; fallback a local `bge-small` via fastembed in the worker (no extra vendor, lower quality).

## 3. Data model (Postgres 17 + pgvector)

Enums: `item_type (solution, service)`, `card_status (provisional, verified, stale, retracted)`,
`evidence_grade (meta_analysis_or_rct, quasi_experimental, observational_or_admin_data, self_reported, anecdotal)`,
`expert_stance (researcher, practitioner, critic)`, `feedback_signal (used, bad_lead, already_covered)`,
`run_status (queued, running, succeeded, partial, failed)`.

```sql
-- taxonomy (versioned, append-only change log)
taxonomy_versions(id, version, created_at, note)
problem_classes(id text pk /* stable slug, never renamed */, label, parent_id, sjn_tag, description,
                introduced_in_version, deprecated_in_version null, status /* active|proposed|deprecated */)
taxonomy_changes(id, version_id, change_type /* propose|accept|reject|deprecate|relabel_display */,
                 class_id, before jsonb, after jsonb, actor /* agent|human:<email> */, reason, created_at)
-- Agents may only INSERT change_type='propose'. Accept/merge/deprecate are human-only (admin API).

problems(id uuid, title, summary, problem_class_ids text[], fips text[], is_national bool,
         source_urls text[], source_adapter, first_seen, last_seen, severity real, novelty real,
         locality_score real, cluster_id uuid, item_type item_type, raw jsonb, embedding vector(1024),
         run_date date)          -- unique(cluster_id, run_date) for idempotency

solution_cards(id uuid, slug, title,
  response_what, response_who text[], response_where_fips text[], response_started_at date,
  insight_mechanism, insight_transferability,
  evidence_grade evidence_grade, problem_class_ids text[], context_tags text[] /* pop band, rural, etc */,
  item_type item_type, status card_status, created_at, last_verified_at, embedding vector(1024),
  drafted_by_run uuid, verified_by_run uuid, version int)
card_evidence(id, card_id, claim, source_url, source_publisher, source_domain, quoted_support_span,
              span_match_score real, source_type /* rct|meta|gov_report|news|org_report|... */,
              fetched_at, content_sha256, archived_url /* web.archive.org snapshot */, verified bool)
card_limitations(id, card_id, limitation, source_url, quoted_support_span, verified bool)
card_versions(card_id, version, snapshot jsonb, changed_at, reason)   -- audit trail, retractions

experts(id, name, role, institution, expertise_tags text[], stance expert_stance,
        contact_path_url /* institutional page or media-relations office */, source_url,
        openalex_id, orcid, last_verified_at, status /* active|moved|unverified */)
  CHECK contact_path_url !~ 'mailto:|tel:'
card_experts(card_id, expert_id, relation /* evaluator_author|practitioner|critic */, source_url)
prior_coverage(id, card_id, outlet, headline, url, published_at)

readers(id, email citext unique, beats text[], coverage_fips text[], is_national bool, cadence,
        timezone, status /* pending|active|unsubscribed|bounced */, created_at, confirmed_at)
briefings(id, reader_id, date, kind /* daily|reply */, items jsonb, rendered_html, rendered_text,
          evaluator_log jsonb, sent_at, provider_message_id)   -- unique(reader_id, date, kind)
feedback(id, reader_id, briefing_id, card_id, signal feedback_signal, note, created_at)
inbound_queries(id, reader_id, provider_message_id unique, raw_text, stripped_text, status, briefing_id, created_at)
runs(id, run_date, stage, status run_status, counts jsonb, errors jsonb, trace_id, started_at, finished_at,
     dry_run bool)              -- unique(run_date, stage, dry_run)
source_fetch_cache(url, fetched_at, http_status, content_sha256, text, etag)   -- verifier + adapters
jobs (owned by the queue library)
```

Indexes: HNSW on both `embedding` columns; GIN on `problem_class_ids`, `fips`, `expertise_tags`.
Feedback links are HMAC-signed tokens (`reader_id, briefing_id, card_id, signal`) — one click, no login.

## 4. Daily pipeline (orchestrator + subagents)

Orchestrator = plain Python (`pipeline/daily.py`), not an LLM. It sequences stages, opens one OTel trace per
run, and calls subagents through the Claude Agent SDK, each with its own fresh context and a narrow tool set.
Every stage takes `(run_date, dry_run)`, reads its inputs from Postgres, writes outputs with upserts keyed on
`run_date`, and records a `runs` row → any stage can be re-run.

| # | Stage | LLM? | Tools given to the subagent | Output |
|---|---|---|---|---|
| 1 | Ingest | classify only (batched, Haiku) | none (adapters are code) | `problems` |
| 2 | Library research | yes (drafter) | `search_openalex`, `fetch_url`, `search_clearinghouse`, `lookup_taxonomy` | provisional card drafts |
| 3 | Verifier | assist only | `fetch_url` (cache), `span_match` (code), `openalex_author` | evidence rows verified/dropped, grade, status |
| 4 | Matcher | no | — | per-reader candidate list |
| 5 | Writer | yes | none — input is a JSON bundle of rows | structured briefing JSON |
| 6 | Evaluator | yes + code checks | none | pass / reasons (≤2 revision loops) |
| 7 | Deliver | no | — | email sent, `sent_at` |
| 8 | Run report | no | — | `runs` summary, alert if deadline missed |

Verifier rules (code-enforced; LLM only proposes the span location and the grade with rationale):
* Each claim's `quoted_support_span` must be found in the fetched source text (HTML→text via trafilatura,
  PDF via pypdf). Not found → claim dropped. All claims dropped → card does not publish.
* Grade assigned from the rubric SKILL.md; the LLM proposes, a rule caps it (e.g., a news article can't
  yield `meta_analysis_or_rct` unless it links a trial that is itself a cited source).
* `verified` requires ≥2 verified evidence rows from independent publishers.
* Weekly re-check: 404/changed hash → re-verify; still missing → `stale`. Expert affiliation changed → `stale`
  on the link, expert marked `moved`.
* Drafter and verifier never share context: separate SDK `query()` sessions, verifier receives only the card
  row + URLs, never the drafter's reasoning.

Failure policy (no human review before send): items failing stage 3 or 6 are dropped individually; if
<1 item survives for a reader, no email that day (logged) rather than an empty or unverified one.

Service guidance is a separate path: only from an allowlist of agency domains (weather.gov, cdc.gov,
ready.gov/fema.gov, state health depts), template-driven, short, never mixed into solution sections.


### 4.1 LLM call strategy (Q11)

* **Agent SDK (`claude-agent-sdk`)** for the agentic stages that need tools and multi-step reasoning: library research
  (drafter), verifier assistance, and the reply-to-query orchestration. The SDK bundles the Claude Code binary, so the
  image needs no Node. Subagents are defined with `AgentDefinition` and given narrow tool lists. Each stage runs as a
  separate `query()` session, so the drafter and verifier never share context.
* **Plain Messages API (`anthropic`)** for the single-shot, high-volume calls: classification, writer and evaluator.
  These need explicit `cache_control` breakpoints on the stable prefix (taxonomy, schema, rubric, style guide); the SDK
  only does caching automatically, with no breakpoints. They also need strict JSON output.
* Knowledge packs live in `skills/*/SKILL.md` (four-pillars, evidence-grading rubric, taxonomy, briefing style guide).
  They are loaded as SDK skills for the agentic stages and inlined as the cached system prefix for Messages API calls,
  so both paths use one source of truth.
* Model choice per stage is a config value, not hard-coded. Haiku-class for classification; the strongest model for
  drafting, verifying and evaluating.


## 5. Fly.io deployment plan

### 5.1 Apps

| App | Purpose | Database |
|---|---|---|
| `solbrief` | production | MPG cluster `solbrief-db` (Basic, $38/mo; PG 17 + pgvector) |
| `solbrief-staging` | staging; the daily run is forced to `DRY_RUN=1` (an env var, plus a DB guard that refuses `sent_at` writes) | MPG cluster `solbrief-staging-db` (Q6) |

Fly Managed Postgres **supports pgvector** (enabled per database), so no Neon or Supabase is needed. The legacy
`fly postgres create` clusters are unmaintained, so we won't use them. Neon (about $15/mo, us-east-1, near `iad`)
is the documented fallback if MPG costs or limits become a problem. Primary region: `ord` (Chicago),
if MPG is offered there (UNVERIFIED); otherwise `iad`.

### 5.2 fly.toml (sketch)

```toml
app = "solbrief"
primary_region = "ord"

[build]
  dockerfile = "Dockerfile"          # python:3.12-slim (glibc; required by the SDK's bundled binary)

[deploy]
  release_command = "alembic upgrade head"   # temp machine, has secrets + network
  release_command_timeout = "10m"

[processes]
  web       = "uvicorn solbrief.web.app:app --host 0.0.0.0 --port 8080"
  worker    = "python -m solbrief.worker"            # procrastinate worker: pipeline stages + reply jobs
  scheduler = "python -m solbrief.scheduler"         # exactly 1 machine

[http_service]
  processes = ["web"]
  internal_port = 8080
  force_https = true
  auto_stop_machines = "stop"
  auto_start_machines = true
  min_machines_running = 1           # webhooks must not cold-start into a timeout

  [[http_service.checks]]
    path = "/healthz"                # checks DB connectivity + queue depth
    interval = "15s"
    timeout = "5s"
    grace_period = "20s"

[[vm]]
  processes = ["worker"]
  memory = "2gb"                     # SDK guidance: ~1 GiB per concurrent agent
  cpus = 2
[[vm]]
  processes = ["web", "scheduler"]
  memory = "512mb"
```

The worker and scheduler groups expose no service, so Fly Proxy never auto-stops them. Scale with
`fly scale count web=1 worker=1 scheduler=1`. **The scheduler must stay at exactly 1 machine**, or every job fires once per copy.

### 5.3 Scheduling

Fly's `--schedule` machines are fuzzy (hourly, daily and so on, with no exact time), and Fly's docs say they're not for precise timing.
So the `scheduler` process group runs **APScheduler** with `CronTrigger(timezone="America/Chicago")`
(`src/solbrief/scheduler.py`):

| Job | When | Does |
|---|---|---|
| `daily_run` | 04:30 America/Chicago | enqueue today's run (procrastinate queueing lock: at most one queued per date) |
| `check_deadline` | every 10 min | after 06:30, if today's run isn't complete, email `ALERT_EMAIL` (once per day) |
| `reverify_library` | weekly (M3) | enqueue re-verification of cards older than 180 days |

The scheduler only **enqueues**; all work happens on the worker. A second line of defense guards against a double-fire:
`daily_run` takes a Postgres advisory lock keyed on the date and returns immediately if that date has already succeeded.

### 5.4 Queue

**procrastinate** (Postgres-backed, maintained, with retries, locks and queueing locks). Its tables are created
in the same Alembic migration chain. No Redis. Queues: `pipeline` (concurrency 1 per stage), `reply` (concurrency 4),
`email`. The web process only ever enqueues.

### 5.5 Secrets (`fly secrets set`, never committed; mirrored in `.env.example`)

`DATABASE_URL`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `POSTMARK_SERVER_TOKEN`, `POSTMARK_INBOUND_WEBHOOK_SECRET`,
`ADMIN_TOKEN`, `HMAC_SECRET` (feedback/unsubscribe links), `LOGFIRE_TOKEN`,
`ALERT_EMAIL`, `OPENALEX_API_KEY`, `BLS_API_KEY`, `CENSUS_API_KEY`, `EIA_API_KEY`, `NWS_USER_AGENT`, `DRY_RUN`.

### 5.6 CI/CD (GitHub Actions)

* `ci.yml` on every PR: ruff, mypy, pytest (unit + adapter contract tests on recorded fixtures), evals
  (matcher recall@5, verifier traps, evaluator cases), and a migration check against an ephemeral `pgvector/pgvector:pg17` service container.
* `deploy.yml` on merge to `main`: `flyctl deploy --remote-only -a solbrief-staging`, then a smoke test (`/healthz`, admin dry-run
  enqueue), then `flyctl deploy -a solbrief`. Uses `superfly/flyctl-actions/setup-flyctl` and a deploy token
  (`fly tokens create deploy`) stored in GitHub secrets as `FLY_API_TOKEN` (one per app). Runs inside `concurrency: deploy`.
* Production deploys can sit behind a GitHub Environment approval if you prefer that to automatic deploys on merge.

### 5.7 Admin API (bearer `ADMIN_TOKEN`)

`POST /admin/runs {date, dry_run}` · `GET /admin/runs/{date}` · `POST /admin/runs/{date}/stages/{stage}/rerun` ·
`GET /admin/taxonomy/proposals` + accept/reject (the only path for taxonomy changes) · `POST /admin/cards/{id}/retract`.

### 5.8 Observability

* One OTel trace per pipeline run; `trace_id` stored on every `runs` row.
* Spans per stage, per reader, per LLM call (model, tokens, cache hits) and per source fetch.
* Logs correlated through the trace context.
* Alert email to `ALERT_EMAIL` when the deadline is missed, the evaluator rejection rate goes over a threshold, or an adapter fails on 2 consecutive days.

---

## 6. Milestones (each demonstrated on staging first, then stop for review)

| # | Deliverable | Demo / exit criteria |
|---|---|---|
| 1 | This plan, source report, schema, Fly plan | **your review** |
| 1b | Repo skeleton, fly.toml (web/worker/scheduler), migrations via release_command, health check, queue, OTel, CI (lint+tests+evals stub), deploy-on-merge, staging app live | `https://<app>-staging.fly.dev/healthz` green; scheduler enqueues a no-op dry run that shows a trace |
| 2 | Ingest: NWS, OpenFEMA, CDC adapters; FIPS geotagging; classification; clustering; scoring | a day's `problems` on staging with FIPS + classes; adapter contract tests with recorded fixtures |
| 3 | Library: Story Tracker seed (pending permission) + one clearinghouse (proposed: CDC Community Guide or J-PAL); drafter + verifier | verifier test set: 100% of planted unsupported claims caught; N verified cards |
| 4 | Matcher + web search + golden set | recall@5 reported in CI; search page on staging |
| 5 | Writer + evaluator + email to test list | evaluator test set 100% rejections; briefings land in test inboxes (Outlook, Gmail, iOS) |
| 6 | Reply-to-query | reply → mini-briefing in < 5 min; per-reader rate limit |
| 7 | Onboarding, feedback, prod scheduled run, `/run-daily-briefing` | first production send to pilot list |

## 7. Evals (built in M3–M5, run in CI on every PR)
* `evals/golden_matcher.jsonl` — ~30 problem statements → expected card ids; metric recall@5, threshold set
  after baseline, CI fails on regression.
* `evals/verifier_traps/` — cards with planted unsupported claims (fabricated stat, real URL but wrong number,
  span from a different page, dead link, paraphrase not in source). Must be 100% caught; CI hard-fail.
* `evals/evaluator_cases/` — briefings missing a pillar / citation / provisional label / mixing item types /
  over length → must all be rejected; plus clean briefings that must pass (false-positive guard).
* LLM calls in CI use recorded responses (VCR) by default; a nightly job runs live against the API.

## 8. Proposed repo layout

```
solbrief/
  adapters/{base.py, nws.py, openfema.py, cdc_rss.py, gdelt.py, ...}   # common SourceAdapter protocol
  geo/        # FIPS gazetteer, CBSA→county crosswalk (Census delineation files), RUCC, pop bands
  taxonomy/   # versioned YAML + loader + change log writer
  pipeline/   # daily.py orchestrator, stages/*.py (each idempotent: run(date, dry_run))
  agents/     # AgentDefinitions + prompts for drafter / verifier / writer / evaluator
  verify/     # fetch+extract (trafilatura, pypdf), span_match, grading caps, independence check
  matcher/    # hybrid retrieval + scoring
  email/      # MJML-free table HTML templates, text alternative, Postmark client, inbound parser
  web/        # FastAPI app, Jinja templates, HTMX
  db/         # SQLAlchemy models, Alembic migrations
skills/       # four-pillars, evidence-rubric, taxonomy, briefing-style SKILL.md
evals/        # golden_matcher.jsonl, verifier_traps/, evaluator_cases/
fly.toml, fly.staging.toml, Dockerfile   # repo root
.github/workflows/
```

## 9. Risks

| Risk | Mitigation |
|---|---|
| The main solution corpus (Story Tracker) is not licensed | Federal clearinghouses first; partnership request in flight; architecture doesn't depend on any one source |
| Fabricated evidence reaches a reporter | Code-level span matching, renderer that only emits row-backed text, a 100% trap-set gate in CI, no unverified fact can be added by the writer |
| Thin library early (few verified cards per class) | Briefings show fewer, better items; "no verified solution yet, see SciLine" fallback; never pad |
| Expert affiliation drift | Re-verify on a schedule; show the verification date; `moved` status removes them from emails |
| CDN blocks on source sites from Fly IPs (several seen today) | Respect them; record per-adapter health; request access or feeds rather than evade |
| LLM cost blow-up | Per-run budget in config with a hard stop; prompt caching; Haiku-class models for classification |
