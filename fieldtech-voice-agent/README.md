# FieldTech Assist — voice agent POC

A voice agent that guides Whirlpool field technicians through appliance repairs over the
phone. This is **POC 2: a voice agent on the same brain** — the agent core is identical to
what a mobile app would use, and voice is a thin surface bolted on top of it.

**Two API keys and you're running.** Brain: **Claude** (Anthropic API). Voice:
**ElevenLabs**. No cloud project, no `gcloud`, no ADC.

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
unzip fieldtech-voice-agent.zip && cd fieldtech-voice-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Two keys
cp .env.example .env         # then paste your keys into it
set -a; source .env; set +a

# 2. Prove the brain is reachable
python scripts/preflight.py

# 3. Build and ingest the synthetic test manual (data/ ships empty)
python scripts/make_test_manual.py
python -m src.ingest.ingest_pdf data/WTW5057LW0_service_manual.pdf --models WTW5057LW0

# 4. Gates — everything except preflight runs keyless
python -m compileall src scripts
python scripts/run_evals.py evals/train/tasks.jsonl
python scripts/run_evals.py evals/holdout/tasks.jsonl
python scripts/smoke_test.py

# 5. Run it — text first, then voice
python -m src.main --text
python -m src.main --voice
```

`--text` and `--voice` both run preflight first and **refuse to start if it fails** — a bad
key or a model your org can't reach should surface as a startup error with remediation, not
as a 404 in the middle of a technician's turn.

On macOS, `--voice` needs PortAudio for `sounddevice`: `brew install portaudio`. `--text`
never imports it.

### Environment

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | **required** — the brain |
| `ELEVENLABS_API_KEY` | required for `--voice` (default provider) |
| `ELEVENLABS_VOICE_ID` | optional voice override |
| `ANTHROPIC_MODEL` | orchestrator model, default `claude-opus-5` |
| `ANTHROPIC_EVAL_MODEL` | retrieval-evaluator model, default `claude-haiku-4-5` |
| `CLAUDE_EFFORT` | `low` (default, voice latency) … `max` |
| `ANTHROPIC_FALLBACKS` | `default` (on). Empty string disables the refusal fallback |
| `LLM_PROVIDER` | `anthropic` (default) or `gemini` |
| `FISH_AUDIO_API_KEY` | only for `--provider fish` |
| `SERVICE_MATTERS_URL` | stage-1 search endpoint override |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | optional; adds OTLP export on top of the JSONL trace file |

### Model choice

Model ids are config, never literals in agent code — ids churn between releases, so a swap
is an env change.

- The orchestrator runs on `claude-opus-5`. For harder diagnostic reasoning raise
  `CLAUDE_EFFORT` before reaching for a different model; expect the extra latency to be
  audible on a phone call.
- The retrieval evaluator runs on `claude-haiku-4-5`. The judge is high-volume and
  low-stakes, so it uses the cheapest model that reliably emits the schema — a deliberate
  per-role choice, not a global downgrade.

Three Claude-specific rules the code enforces, each of which is a 400 or a silent
misbehaviour if you get it wrong:

- **No `temperature` / `top_p` / `top_k`.** Removed on current models — they return a 400.
  Depth is `output_config.effort`.
- **Thinking stays on.** It's on by default and `max_tokens` caps thinking + reply text
  together. Disabling it can make the model write a tool call into its visible text — the
  call then silently never runs — or leak `<thinking>` tags into a spoken reply. Lower
  `effort` instead; that's the cheaper lever anyway.
- **No assistant prefill.** Structured output is `output_config.format` with a JSON schema.

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
          -> retrieval evaluator fires on the cheap model, returns
             NEEDS_RERETRIEVAL or ESCALATE — the orchestrator never sees its exploration

tech>  goodbye
          -> salesforce_writeback appends the resolution to data/writeback_log.jsonl
```

Watch it work: `tail -f observability/traces/spans.jsonl | jq`.

## Architecture

```
voice (elevenlabs | fish)  ──┐          the ONLY layer that knows audio exists
  src/voice/audio_io.py      │          telephony (SIP/Twilio) replaces this file alone
                             ▼
        Orchestrator  src/agent/orchestrator.py      hand-rolled loop, auto-exec OFF
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
   post: weak manual_search  ──▶  retrieval evaluator (SEPARATE model call,
                                   own context, ≤2 refinement searches, JSON verdict)

  everything above talks to  src/agent/llm/LLMClient  ──▶  claude | gemini
```

**Automatic tool execution is off on purpose.** Owning the dispatcher is what makes the
hooks enforceable in code rather than in prompt text: the pre-hook raises on a write into
`evals/**` or `observability/manifest/**` without asking the model, and the post-hook fires
the retrieval evaluator whether or not the model would have thought to.

Skills are separate markdown files, not inlined strings, because the future app agent loads
the same files and because they port into `.claude/skills/` unchanged.

## Real vs stubbed

| Piece | Status | Note |
|---|---|---|
| Claude brain (Anthropic API) | **real** | API key only; no sampling params, thinking on, refusal fallback enabled |
| Gemini brain (Model Garden) | **real** | `LLM_PROVIDER=gemini`; ADC only, AI Studio keys hard-rejected |
| Preflight per-model reachability | **real** | `count_tokens` against your key |
| Hand-rolled tool loop, auto-exec off | **real** | ≤15 tool rounds/turn, then a forced finish |
| Hooks (pre write-block, post evaluator trigger) | **real** | verified by `scripts/smoke_test.py` |
| Retrieval evaluator subagent | **real** | separate model call, JSON-schema-constrained verdict |
| Skills loaded from markdown | **real** | frontmatter parsed, appended to system prompt |
| OTel spans | **real** | JSONL always; OTLP when the endpoint env var is set |
| PDF ingestion | **real, thin** | pdfplumber page text; Document AI replaces it later |
| Figure extraction | **real** | embedded images cropped to PNG |
| `manual_search` | **real, BM25** | a vector index replaces the internals; contract holds |
| Evals + grader | **real, keyless** | deterministic; no model calls |
| ElevenLabs / Fish Audio clients | **real** | written against verified current endpoints; unrun by me (no keys) |
| Mic + speaker I/O | **real** | `sounddevice`; telephony replaces this file |
| `salesforce_lookup` / `salesforce_writeback` | **stubbed** | JSON fixture in, JSONL out |
| `service_matters_search` | **stubbed in practice** | real HTTP call, falls back to the local registry offline |
| `get_figure` push | **stubbed** | logs `app_push` to `data/figure_pushes.jsonl` |
| Corpus | **synthetic** | one 3-page generated manual, not a real Whirlpool doc |
| Telephony (SIP/Twilio) | **not built** | seam is `src/voice/audio_io.py` |

### Brain-swap path

This is demonstrated, not asserted: `LLM_PROVIDER=gemini` runs the **same** orchestrator,
dispatcher, hooks, skills, agent prompt, tool contracts and evals against Gemini on Model
Garden. Only `src/agent/llm/<provider>_client.py` differs. Claude is also served in Model
Garden, so an enterprise tenant that wants cloud-native auth can keep its project and
credentials and still change which model answers.

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
.env.example                 copy to .env, paste two keys
agents/retrieval-evaluator.md    subagent prompt
skills/voice-turns.md            turn shaping (≤2 sentences, no markdown, read-backs)
skills/safety-callouts.md        verbatim quoting, citation, the stop condition
fixtures/salesforce_cases.json   T-1001 Dana Ruiz / WTW5057LW0 / CX4412873
src/agent/llm/               the brain seam: base, anthropic_client, gemini_client
src/agent/                   orchestrator, prompts, skills loader, tool schemas,
                             hooks, dispatcher, retrieval evaluator
src/tools/                   the five tools
src/ingest/ingest_pdf.py     pdfplumber -> page chunks + cropped figures
src/voice/                   VoiceProvider ABC, elevenlabs, fish, mic/speaker I/O
src/telemetry/               span() -> spans.jsonl (+ OTLP)
scripts/                     preflight, run_evals, make_test_manual, smoke_test
```
