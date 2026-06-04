# fpna-agents — The Agentic Office of the CFO (FP&A Decision Layer)

A multi-agent FP&A system on the Claude Agent SDK for **variance analysis,
reforecasting, driver/sensitivity analysis, and board-meeting materials** —
delivered through morning briefings, a conversational interface, and meeting-prep
dossiers.

> **The one invariant (§0):** No number is ever produced by the model. Every figure
> that leaves this system is either **read directly from an Anaplan line item**, or
> **computed by Anaplan's engine** (Calcite SQL or a scenario recompute), and carries
> provenance back to its exact intersection. This is a *harness guarantee*, enforced by
> hooks the model cannot reason around — not a request in a prompt.

## Status — Sprint 0 (vertical slice) ✅

Sprint 0 proves the invariant before adding surface area:

- **`anaplan-retriever`** subagent — the single chokepoint that touches Anaplan.
- **Provenance ledger** (`.fpna/ledger/<session>.jsonl`) — `src/ledger/`, the allowlist of speakable numbers.
- **Hooks (the Hashimoto core, `.claude/hooks/`):**
  - `ledger-append.py` (PostToolUse) — every Anaplan value becomes an `AnaplanFact`.
  - `no-math-gate.sh` (PreToolUse/Bash) — blocks LLM/Python/shell financial arithmetic.
  - `provenance-stop-gate.py` (Stop) — blocks any output number not in the ledger.
  - `scenario-write-guard.sh` (PreToolUse) — denies destructive/prod/unapproved writes (Phase 2).
  - `backup-ledger.sh` (PreCompact) — the ledger survives context loss.
- **Variance Analysis (§8.1)** route + `/variance` command on the conversational interface.
- **31 acceptance tests** (`tests/hooks/`) — including the planted LLM-subtraction and the
  planted unsourced number, both **blocked**.

```
npm install
npm test              # 31/31 — gates block their planted violations
npm run typecheck
```

> ⚠️ The gates are verified locally (pure logic). The end-to-end "answer a real variance
> question on a **live** Anaplan model" leg of Sprint 0 needs Chimera credentials (below) —
> it is not exercisable in CI without a Dev/sandbox model.

## Setup

1. **Credentials (never committed):** copy `.claude/settings.local.json.example` →
   `.claude/settings.local.json` and fill in `ANAPLAN_BASIC_AUTH` (= `base64(username:password)`),
   `ANAPLAN_WS_GUID`, `ANAPLAN_MODEL_GUID` (**Dev/sandbox only**). See `docs/anaplan-mcp-setup/`
   for the helper that injects Basic auth without it touching your shell history.
2. **MCP:** `anaplan-chimera` (primary, Calcite SQL) is wired in `.claude/settings.json`.
   `anaplan-ops` (bulk/actions, Bearer token) lands in **Phase 2** — set `ANAPLAN_OPS_TOKEN`
   then add its server block (see `docs/agentic-harnessing-guide.md` / kickoff §11).

## How it works (the calculation hierarchy, §4)
A delta/variance/%/YoY is sourced, never computed: **(1)** read a structural line item that
already holds it (prefer REP/OUT modules) → **(2)** if none, one Calcite query returns it
`AS <alias>` (the engine subtracts) → **(3)** LLM arithmetic is *forbidden* and blocked.

## Layout
```
.claude/agents/      anaplan-retriever (more in Phase 1+)
.claude/commands/    /variance (+ /reforecast /sensitivity /board /prep /briefing scaffolds)
.claude/hooks/       the four enforcement hooks + shared helper
.claude/skills/      4 vendored Chimera skills + numeric-provenance + anaplan-variance-bridge
.claude/settings.json  MCP wiring + hooks + permissions
src/ledger/          AnaplanFact contract + ledger store + CLI
src/orchestrator.ts  routing + effort-scaling + ledger ownership
src/mcp/chimera.ts   Chimera connection config
tests/hooks/         gate acceptance tests (planted-violation proofs)
docs/                vendored MCP setup package + agentic harnessing guide
telemetry/           OTel collector config (OTel GenAI spans, §12)
evals/               provenance evals + trigger tests + trace replays (§13)
```

See `CLAUDE.md` for the operating spine and the gotcha that costs hours.
