# CLAUDE.md — FP&A Decision Layer (the spine)

## THE ONE INVARIANT (§0)
> **No number is ever produced by the model. Every figure that leaves this system is
> either read directly from an Anaplan line item, or computed by Anaplan's calculation
> engine (Calcite SQL or a scenario recompute), and carries provenance back to its exact
> intersection.**

This is a harness guarantee enforced by hooks you cannot reason around (Hashimoto
principle: a mistake that *can* happen *will* happen — so we make it structurally
impossible). Your job: decide **which** numbers to ask Anaplan for, **how** to slice them,
and **how** to narrate them. Never originate, sum, difference, multiply, divide, ratio,
annualize, or extrapolate a value.

## Golden principles
1. The model never makes a number. It decides which to fetch and how to say it.
2. Arithmetic is a query, not a thought. Variance/%/YoY come from a structural line item or one Calcite query — Anaplan computes.
3. Reforecast = write → recompute → read. Never extrapolate.
4. Sensitivity = many recomputes. Never interpolate.
5. Every output number carries provenance back to its intersection. No ledger entry, no mention.
6. Writes are gated, approved, sandboxed, versioned. Destructive ops are denied to agents entirely.
7. Isolate retrieval. One agent (`anaplan-retriever`) touches Anaplan; the rest consume `AnaplanFact[]`.
8. Enforce in hooks, not prompts.
9. Skills are trainable. Initialize, then optimize against a held-out provenance gate.
10. Narrate the *why*, source the *what*.

## Routing rule
- **read or compare** → Chimera `aocfo_sql_query` (engine does the math).
- **drill a formula / why did it move** → Chimera `aocfo_explain_cell`.
- **single/small what-if write** → Chimera `aocfo_cell_write` (gated, approved, sandbox).
- **bulk scenario load / >1M-cell read / export** → `anaplan-ops` (Phase 2+).
- **anything destructive** → blocked at the hook layer. Never attempt.

## The calculation hierarchy (§4) — walk in order, stop at first that works
1. **Structure first (zero arithmetic):** catalog modules (prefer REP/OUT report modules) →
   find a line item literally named *Variance/Delta/YoY/%Change/Growth/Contribution* → read it. The cell IS the answer.
2. **Calcite computes it:** if no structural line item, emit ONE SQL query returning the derived value
   (`a."revenue" - b."revenue" AS rev_delta`). The subtraction happens in Anaplan.
3. **Forbidden:** LLM/Python/shell arithmetic on values from two result sets. Blocked by `no-math-gate`.

## The AnaplanFact contract (§3)
Every number travels as a provenanced fact: `value` (verbatim, immutable), `label`, `module`,
`lineItem`, `intersection`, `version`, `source` (`line_item`|`sql_calcite`|`scenario_recompute`|`explain_cell`),
`query` (if SQL), `requestId`, `fetchedAt`. Definition: `src/ledger/fact.ts`. The run-scoped
**provenance ledger** (`.fpna/ledger/<session>.jsonl`) is the allowlist: a number may appear in
output **iff** it is in the ledger. Narrative emits `{{fact:<requestId>}}` placeholders, never typed numerals.

## Writes
Writes go **only** to sandbox **Forecast/Budget/named-scenario** versions, **always approved**
(`approved:true` from human confirmation, or pre-granted in a signed cron config). Never write to a
production model or the Actual/Current version. `scenario-write-guard` enforces this.

## Capabilities (one-line index) — §8
- **Variance** (8.1): retrieve variance → bridge + driver ranking → commentary → evaluate → deliver.
- **Reforecast** (8.2): write drivers → recompute → read → compare → narrate.
- **Sensitivity** (8.3): N variants, each write→recompute→read; assemble grid from engine reads.
- **Board materials** (8.4): bulk retrieve → analyze → narrate w/ placeholders → assemble deck + provenance appendix.

## Delivery modes (§9)
Morning briefing (headless cron, read-only) · Conversational interface (ad-hoc, `explain_cell`-heavy) ·
Meeting-prep dossier (pre-fetched Q&A pack).

## THE GOTCHA THAT COSTS HOURS
The Chimera Calcite SQL surface requires **`included_objects` on every schema and query call**,
**slice-XOR-leaf per dimension** (filter a dimension by slice value OR read it at leaf, never both),
and **never aliases `cur`/`prev`** (use `fy26`/`fy25`). No CTEs.

## Commands
- Build / typecheck: `npm run build` · `npm run typecheck`
- Tests (incl. hook gates): `npm test` · hooks only: `npm run test:hooks`
- Inspect ledger: `npm run ledger:show <session-id>`
- Slash commands: `/variance <grain>` `/reforecast` `/sensitivity <drivers>` `/board <period>` `/prep <meeting>` `/briefing`

Everything else lives in skills (triggered, `.claude/skills/`) and docs (referenced), not here.
