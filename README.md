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

## Web app (conversational interface)

A live chat UI with a **provenance ledger panel** that shows every `AnaplanFact`
behind the answer — value, source (line item / Calcite SQL / recompute / explain),
version, intersection, and the exact SQL the engine ran.

- **Backend** (`src/web/`): a lean Node server driving the Claude Agent SDK
  (`@anthropic-ai/claude-agent-sdk`). The SDK does **not** auto-load `settings.json`
  MCP servers or hooks, so the backend wires them **explicitly** (`mcp/chimera.ts`,
  `web/hooks-bridge.ts`) — running the *same* `.claude/hooks/` scripts, so the web
  path enforces the §0 invariant identically to the CLI (ledger-append, no-math
  gate, provenance Stop-gate all active). In the web path the lead **retrieves
  directly** via the Anaplan tools (rather than delegating to the `anaplan-retriever`
  subagent), so every raw MCP response lands in the ledger and can be verified.
  Read-only: writes/mutations are denied (Phase 2).
- **Frontend** (`web/`): React + the Anaplan Design System (`@ads/react`,
  `@ads/sass`, `@ads/icons`) via Vite.

**Easiest (macOS, no command line):** double-click `scripts/setup-mac.command`
(enter credentials in dialog boxes), then double-click `scripts/start-mac.command`
(installs deps, launches, opens the browser). Uses the built-in UI below.

**Built-in UI:** the backend serves a zero-dependency UI from `public/` — no
frontend build, no private `@ads/*` packages. Just `npm run web` → open
`http://localhost:8787`. The ADS React app (`web/`) is served instead *if* you
build it (`cd web && pnpm install && pnpm build`).

**Run it (terminal):**
```bash
# 1. credentials — backend env (gitignored settings.local.json + your API key)
cp .claude/settings.local.json.example .claude/settings.local.json   # fill Anaplan GUIDs + Basic auth (Dev/sandbox)
export ANTHROPIC_API_KEY=sk-ant-...                                  # or put it in settings.local.json env

# 2. backend + built-in UI on :8787
npm install && npm run web

# 3. (optional) ADS React dev server with hot reload — needs Anaplan registry access for @ads/*
cd web && pnpm install && pnpm dev        # http://localhost:5173
```
`GET /api/health` reports readiness; the UI shows **live** once Anaplan creds +
an API key are present. Configure `FPNA_MODEL` to override the model
(default `claude-sonnet-4-6`; set an Opus tier if your key has access),
`FPNA_WEB_PORT` to change the port.

> The `@ads/*` packages resolve from Anaplan's internal registry (`workspace:^1.1.0`,
> per `docs/anaplan-mcp-setup`); install them where you have that access.

### Two Anaplan surfaces
The web backend can read Anaplan through either MCP surface; it **prefers the ops
server when `ANAPLAN_OPS_TOKEN` is set**, else falls back to Chimera.

| Surface | Endpoint | Auth | Reach | Tools |
|---|---|---|---|---|
| **anaplan-ops** (preferred) | public Azure (`anaplan-mcp…azurecontainerapps.io`) | Bearer token | **No VPN**, plain HTTP, no subprocess | `show_*`, `read_cells`, `run_export` (read-only; mutations denied) |
| **anaplan-chimera** | internal (`us1a.app-chimera.anaplan.com`) | Basic auth | **Needs Anaplan VPN**, via `mcp-remote` | Calcite SQL `aocfo_*` |

Either way the host must be able to reach the chosen endpoint. A restricted egress
allowlist returns `Host not in allowlist (403)` and the MCP server shows
`status: failed`. The chat UI surfaces this connection status on the first turn;
`GET /api/health` reports which `surface` is configured. The **ops** surface has
no SQL, so variance is read from a stored line item (structure-first), never computed.

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
