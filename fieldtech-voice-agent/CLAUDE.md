# CLAUDE.md — FieldTech Assist Voice Agent (POC 2)

A voice agent that guides Whirlpool field technicians through appliance repairs over the
phone. **The agent core is what a mobile app would use; voice is a thin surface on top.**
Everything below the `VoiceProvider` seam is shared with the future app agent.

This file is for Claude Code sessions working on this repo. The *runtime brain is Gemini*
(Model Garden), not Claude — don't confuse the two.

## THE ONE INVARIANT
> **The agent never invents a repair instruction and never paraphrases a safety warning.**
> Every step it speaks comes from a retrieved manual chunk, cited by doc + page. Every
> WARNING/CAUTION is quoted verbatim from the source text. If no authoritative safety text
> is retrievable for a hazardous step, the agent offers a warm transfer and stops.

## Model policy — Model Garden only
- Initialize `google-genai` in **Vertex / Gemini Enterprise Agent Platform mode only**:
  `GOOGLE_GENAI_USE_VERTEXAI=true` (the SDK also accepts the newer
  `GOOGLE_GENAI_USE_ENTERPRISE=true`; it wins on conflict), plus `GOOGLE_CLOUD_PROJECT`,
  `GOOGLE_CLOUD_LOCATION`, and Application Default Credentials.
- **Never** support or fall back to `GEMINI_API_KEY` / AI Studio. The production tenant
  serves models exclusively through Model Garden, and the POC must exercise the same path
  (project enablement, quotas, regional availability included). `src/agent/model_client.py`
  raises if an AI Studio key is the only credential present.
- **Model ids are config, never literals in agent code.** Garden names churn
  (2.5 → 3 → 3.5 → 3.6 inside a year); a swap must be an env change.
  - Orchestrator: `GEMINI_MODEL`, default `gemini-3.5-flash`.
  - Retrieval evaluator: `GEMINI_EVAL_MODEL`, default `gemini-3.5-flash-lite`.
- `scripts/preflight.py` proves each configured model is reachable in *this* project and
  region before any session starts. `--text` and `--voice` refuse to run if it fails.

## Architecture — five layers (we build the harness; there is no `.claude/` runtime)
1. **Tools** (`src/tools/`) — `salesforce_lookup`, `service_matters_search`, `manual_search`,
   `get_figure`, `salesforce_writeback`. Declared as Gemini function declarations in
   `src/agent/tool_schemas.py`. Invoked **only** through the dispatcher.
2. **Skills** (`skills/*.md`) — markdown + frontmatter, loaded at session start and appended
   to the system prompt. Keep them as separate files, never inlined strings: the future app
   agent reuses them, and they port 1:1 into `.claude/skills/` if the brain moves to Claude.
3. **Agent** (`src/agent/orchestrator.py`, prompt in `src/agent/prompts.py`) — hand-rolled
   tool loop. Automatic function calling is **OFF**; we dispatch every call ourselves,
   max `MAX_TOOL_ROUNDS` rounds per user turn. Owning the dispatcher is what makes the
   hooks enforceable in code instead of in prompt text.
4. **Subagent** (`src/agent/retrieval_evaluator.py`, prompt in `agents/retrieval-evaluator.md`)
   — a SEPARATE Gemini call on `GEMINI_EVAL_MODEL` with its own context. LLM-as-judge on
   retrieval quality. Its exploration never enters the orchestrator's context; only the
   verdict does.
5. **No agent teams.** Out of scope.

## Hooks over prompts
`src/agent/hooks.py` is called deterministically by the dispatcher around **every** tool call:
- **pre-hook** — blocks any tool from writing under `evals/**` or `observability/manifest/**`.
  It raises; it does not ask the model. Path-writing helpers also call `guard_write_path()`
  directly, so the boundary holds even off the dispatcher path.
- **post-hook** — on `manual_search`, auto-triggers the retrieval evaluator when top scores
  are weak or results span multiple `doc_id`s.

Hooks are code the model cannot reason around. If you find yourself writing "the model
should remember to…", that belongs in a hook.

## The read-only boundary
`evals/` is **read-only** — for the agent and for you. Write the task files once, then never
touch them. `evals/holdout/` is never used to tune anything. `observability/manifest/` is
likewise off-limits to tools.

## Telemetry
`src/telemetry/span()` wraps every tool call, model call, agent turn, STT and TTS call.
Always appends JSON to `observability/traces/spans.jsonl`; additionally exports OTLP when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set. **Telemetry never fails the call path** — a broken
exporter must not drop a technician's call.

## Stable seams (where production replaces POC)
| Seam | POC | Production |
|---|---|---|
| `VoiceProvider` (`src/voice/base.py`) | mic + speaker | SIP / Twilio; only the audio I/O file changes |
| `manual_search` internals | BM25 over page chunks | Vertex vector index / hybrid; tool contract unchanged |
| `src/ingest/ingest_pdf.py` | pdfplumber page text | Document AI layout parser |
| `salesforce_*` | JSON fixtures | Salesforce REST |
| `get_figure` push | `data/figure_pushes.jsonl` | real push to the technician's app |
| model client | Gemini on Model Garden | Claude is served in the *same* Model Garden — swapping the brain changes `model_client.py` only; tenant, auth, skills and tool contracts stay |

Leave a seam and a one-line comment where production differs. Don't build an abstraction
for it.

## Commands
```
python -m compileall src scripts          # syntax gate
python scripts/preflight.py               # every configured model must PASS
python scripts/make_test_manual.py        # synthetic 3-page manual
python -m src.ingest.ingest_pdf <pdf> --models WTW5057LW0
python scripts/run_evals.py evals/train/tasks.jsonl      # keyless, deterministic
python scripts/run_evals.py evals/holdout/tasks.jsonl
python -m src.main --text
python -m src.main --voice --provider fish|elevenlabs
```
