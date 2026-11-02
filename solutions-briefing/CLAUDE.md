# CLAUDE.md — Solutions Briefing

Daily solutions-journalism briefing for US reporters (Knight Lab). Read `docs/PLAN.md` before changing behavior.

## The trust invariant
A single invented piece of evidence ends trust in the product. Therefore:
- No fact reaches a reader unless it is backed by a DB row whose `quoted_support_span` was found in the
  fetched source **by code** (the verifier, M3), not judged by a model.
- The writer may only use library cards and problem records passed to it; every factual sentence cites a row id.
- Never mix item types: *Solution lead* vs *Service guidance* (agency sources only).
- `provisional` cards are always labeled; `stale`/`retracted` never appear.
- Experts: institutional pages / media-relations offices only. Never store personal emails or phones
  (enforced by a DB CHECK — don't weaken it).
- Taxonomy: agents may only *propose* classes (DB CHECK). Never rename or merge existing class ids.

## Sources
Respect each source's terms (docs/PLAN.md §1). Do not scrape around a block, robots rule, or CDN challenge.
RED sources (no adapter without written permission): SJN Story Tracker, Cochrane, CDC HAN pages.

## Engineering
- Python 3.12, uv. `uv sync --extra dev`; `uv run ruff check . && uv run pytest` (needs Postgres+pgvector,
  see README). Schema changes: edit `db/models.py`, `uv run alembic revision --autogenerate`, review, and
  make sure `alembic check` is clean.
- Every pipeline stage is idempotent for `(run_date, dry_run)` and records a `runs` row.
- Staging never sends (APP_ENV=staging forces dry run; DB refuses `sent_at` on dry-run briefings).
- Secrets only via env / `fly secrets set`. Never commit them.
- Each milestone: demonstrate on staging, then stop and report.
