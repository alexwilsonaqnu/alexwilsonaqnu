# FieldTech Assist — voice agent POC

A voice agent that guides Whirlpool field technicians through appliance repairs over the
phone. This is **POC 2: a voice agent on the same brain** — the agent core is identical to
what a mobile app would use, and voice is a thin surface bolted on top of it.

The brain runs on **Gemini served from Model Garden on the Gemini Enterprise Agent
Platform** (the post-April-2026 name for Vertex AI). There is no AI Studio path: the
production tenant serves models exclusively through Model Garden, so the POC exercises the
same project enablement, quotas and regional availability. The voice layer is **Fish Audio**
by default.

## What it does

1. **Identifies** — technician id → Salesforce case → serial number → model. If the
   technician is unknown, it falls back to free-text or partial-model disambiguation, and
   confirms the model number back digit by digit before searching.
2. **Retrieves** — routes the confirmed model to candidate manuals via a stage-1
   ServiceMatters search, then runs hybrid search *within* those documents.
3. **Guides** — one step per turn, confirmation before advancing, doc + page cited on
   first use, safety warnings quoted **verbatim** from the source. Diagrams can't be spoken,
   so the agent pushes the figure to the technician's app and logs the delivery.

## Quickstart

```bash
cd fieldtech-voice-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Point at your Model Garden tenant (no API keys — ADC only)
export GOOGLE_GENAI_USE_VERTEXAI=true      # SDK also accepts GOOGLE_GENAI_USE_ENTERPRISE=true
export GOOGLE_CLOUD_PROJECT=<your-project>
export GOOGLE_CLOUD_LOCATION=global        # or a region where your models are enabled
gcloud auth application-default login

# 2. Prove every configured model is reachable in THIS project/region
python scripts/preflight.py

# 3. Build and ingest the synthetic test manual
python scripts/make_test_manual.py
python -m src.ingest.ingest_pdf data/WTW5057LW0_service_manual.pdf --models WTW5057LW0

# 4. Gates (all keyless except preflight)
python -m compileall src scripts
python scripts/run_evals.py evals/train/tasks.jsonl
python scripts/run_evals.py evals/holdout/tasks.jsonl
python scripts/smoke_test.py

# 5. Run it
python -m src.main --text
python -m src.main --voice --provider fish     # needs FISH_AUDIO_API_KEY
```

`--text` and `--voice` both run preflight first and **refuse to start if it fails** — a
missing Garden enablement should surface as a startup error with remediation, not as a 404
in the middle of a technician's turn.

### Environment

| Variable | Purpose |
|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` / `GOOGLE_GENAI_USE_ENTERPRISE` | must be `true`; Model Garden mode |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | tenant and region (`global` is fine) |
| `GEMINI_MODEL` | orchestrator model, default `gemini-3.5-flash` |
| `GEMINI_EVAL_MODEL` | retrieval-evaluator model, default `gemini-3.5-flash-lite` |
| `SERVICE_MATTERS_URL` | stage-1 search endpoint override |
| `FISH_AUDIO_API_KEY`, `FISH_AUDIO_VOICE_ID` | Fish Audio (default provider) |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | ElevenLabs (`--provider elevenlabs`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | optional; adds OTLP export on top of the JSONL trace file |

**Setting `GEMINI_API_KEY` or `GOOGLE_API_KEY` is a hard error**, not a fallback. Unset it.

### Model choice

Model ids are config, never literals in agent code — Garden names churn (2.5 → 3 → 3.5 →
3.6 within a year), so a swap is an env change. GA ids verified in Model Garden as of
2026-08: `gemini-3.5-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-pro`.

- Default orchestrator is `gemini-3.5-flash`: function calling plus voice-appropriate
  latency.
- For harder diagnostic reasoning, `GEMINI_MODEL=gemini-3.1-pro` is the current Pro GA id.
  Expect the extra latency to be audible on a phone call.
- The retrieval evaluator is high-volume and low-stakes, so it runs on the cheapest Garden
  model that reliably emits the JSON schema.

## Demo script (`--text` mode)

```
agent> FieldTech Assist here. Can I get your technician id to pull up your case?

tech>  technician T-1001
          -> salesforce_lookup: Dana Ruiz, case 5003X00001Kq9ZA,
             WTW5057LW0 / CX4412873, "fills but does not agitate; F7E1 intermittent"
          -> agent confirms the model number character by character and asks for a read-back

tech>  yes that's right, what should I check first?
          -> service_matters_search(WTW5057LW0) -> candidate doc ids
          -> manual_search(...) -> page 1, shifter assembly / F7E1 / drive belt
          -> ONE step, cited: "That's from the WTW5057LW0 service manual, page one."
             ends with a confirmation question

tech>  ok, now I need to get into the console
          -> manual_search surfaces the safety-flagged page 2
          -> "The manual says, quote: WARNING: Electrical Shock Hazard. Disconnect power
             before servicing." — verbatim, then waits for verbal confirmation

tech>  can you show me where the capacitor is?
          -> get_figure -> "I'm pushing that diagram to your app now."
             delivery appended to data/figure_pushes.jsonl

tech>  it's making a noise
          -> weak retrieval (top BM25 score ~0.12) trips the post-hook
          -> retrieval evaluator fires on GEMINI_EVAL_MODEL, returns
             NEEDS_RERETRIEVAL / ESCALATE — the orchestrator never sees its exploration

tech>  goodbye
          -> salesforce_writeback appends the resolution to data/writeback_log.jsonl
```

Watch it work: `tail -f observability/traces/spans.jsonl | jq`.

## Architecture

```
voice (fish | elevenlabs)  ──┐          the ONLY layer that knows audio exists
  src/voice/audio_io.py      │          telephony (SIP/Twilio) replaces this file alone
                             ▼
        Orchestrator  src/agent/orchestrator.py      hand-rolled loop, AFC OFF
             │  system prompt: src/agent/prompts.py
             │  + skills/voice-turns.md, skills/safety-callouts.md
             ▼
         Dispatcher  src/agent/dispatcher.py         every tool call goes through here
             │
      ┌──────┴───────┐
      ▼              ▼
   hooks.py       src/tools/       salesforce_lookup · service_matters_search
   pre:  block                      manual_search · get_figure · salesforce_writeback
     writes to evals/**
   post: weak manual_search  ──▶  retrieval evaluator (SEPARATE Gemini call,
                                   own context, ≤2 refinement searches, JSON verdict)
```

**Automatic function calling is off on purpose.** Owning the dispatcher is what makes the
hooks enforceable in code rather than in prompt text: the pre-hook raises on a write into
`evals/**` or `observability/manifest/**` without asking the model, and the post-hook fires
the retrieval evaluator whether or not the model would have thought to.

Skills are separate markdown files, not inlined strings, because the future app agent loads
the same files and because they port into `.claude/skills/` unchanged.

## Real vs stubbed

| Piece | Status | Note |
|---|---|---|
| Model Garden client, Vertex-only auth | **real** | ADC; AI Studio keys hard-rejected |
| Preflight per-model reachability check | **real** | `count_tokens` against your tenant |
| Hand-rolled tool loop, AFC disabled | **real** | ≤15 tool rounds/turn, then a forced finish |
| Hooks (pre write-block, post evaluator trigger) | **real** | verified by `scripts/smoke_test.py` |
| Retrieval evaluator subagent | **real** | separate model call, JSON response schema |
| Skills loaded from markdown | **real** | frontmatter parsed, appended to system prompt |
| OTel spans | **real** | JSONL always; OTLP when the endpoint env var is set |
| PDF ingestion | **real, thin** | pdfplumber page text; Document AI replaces it later |
| Figure extraction | **real** | embedded images cropped to PNG |
| `manual_search` | **real, BM25** | Vertex vector index replaces the internals; contract holds |
| Evals + grader | **real, keyless** | deterministic; no model calls |
| Fish Audio / ElevenLabs clients | **real** | written against verified current endpoints; unrun here (no keys) |
| Mic + speaker I/O | **real** | `sounddevice`; telephony replaces this file |
| `salesforce_lookup` / `salesforce_writeback` | **stubbed** | JSON fixture in, JSONL out |
| `service_matters_search` | **stubbed in practice** | real HTTP call, falls back to the local registry offline |
| `get_figure` push | **stubbed** | logs `app_push` to `data/figure_pushes.jsonl` |
| Corpus | **synthetic** | one 3-page generated manual, not a real Whirlpool doc |
| Telephony (SIP/Twilio) | **not built** | seam is `src/voice/audio_io.py` |

### Brain-swap path

Claude models are served from the **same** Model Garden. Moving the brain to Claude changes
the model client (`src/agent/model_client.py`) and the function-declaration format
(`src/agent/tool_schemas.py`) — tenant, auth, prompts, skills, agent files and tool
contracts all stay as they are, and `skills/*.md` plus `agents/*.md` port into `.claude/`
as-is.

## Evals

`evals/train/tasks.jsonl` (4 tasks) and `evals/holdout/tasks.jsonl` (3 tasks) cover the
WTW5057LW0 scenarios: agitation failure, F7E1, drive belt, capacitor discharge (`safety`),
lid lock, inlet valve, and electrical-shock safety.

The grader is deterministic and makes **no model calls**, so it runs keyless. Per task it
checks that the expected doc appears in the top-k, that an expected signal string appears
in the top-k text, that safety tasks surface a safety-flagged chunk, and that the **rank-1**
hit carries a signal string — the last check is what keeps it a ranking test rather than a
recall test on a three-page corpus.

`evals/` is **read-only**, to the agent and to anyone working in the repo; the pre-hook
enforces it. `evals/holdout/` is never used to tune anything.

## Layout

```
CLAUDE.md                    conventions, invariant, read-only boundary
agents/retrieval-evaluator.md    subagent prompt
skills/voice-turns.md            turn shaping (≤2 sentences, no markdown, read-backs)
skills/safety-callouts.md        verbatim quoting, citation, the stop condition
fixtures/salesforce_cases.json   T-1001 Dana Ruiz / WTW5057LW0 / CX4412873
src/agent/                   orchestrator, prompts, skills loader, tool schemas,
                             hooks, dispatcher, retrieval evaluator, model client
src/tools/                   the five tools
src/ingest/ingest_pdf.py     pdfplumber -> page chunks + cropped figures
src/voice/                   VoiceProvider ABC, fish, elevenlabs, mic/speaker I/O
src/telemetry/               span() -> spans.jsonl (+ OTLP)
scripts/                     preflight, run_evals, make_test_manual, smoke_test
```
