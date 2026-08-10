# Demo script

Everything below is fixture data — a synthetic technician, a synthetic case, and a
generated 3-page manual. Nothing here is a real Whirlpool document.

## Identifiers

| | |
|---|---|
| **Technician ID** | `T-1001` — Dana Ruiz, Midwest / IL-3 |
| **Model number** | `WTW5057LW0` |
| Serial | `CX4412873` |
| Case | `5003X00001Kq9ZA` |
| Reported issue | fills but does not agitate; F7E1 intermittent |
| **Unknown technician** | `T-1042` — Priya Nandakumar, no open case |

The UI reads these from `fixtures/salesforce_cases.json` and shows them on a demo card, so
the card can never drift from what `salesforce_lookup` actually returns.

## What's in the manual

| Page | Content | Safety-flagged |
|---|---|---|
| 1 | Fills-but-won't-agitate diagnostics · shifter assembly · fault code `F7E1` · drive belt · part `W11035747` | no |
| 2 | `WARNING: Electrical Shock Hazard. Disconnect power before servicing.` · capacitor discharge · wiring schematic (the pushable figure) | **yes** |
| 3 | Lid lock · fault code `F5E2` · part `W11307244` · water inlet valve · 800–1300 ohms | no |

The only extractable figure is `wtw5057lw0_service_manual_p2_fig1` — the page-2 wiring
schematic. That's what appears in the app panel.

## Run it

```bash
set -a; source .env; set +a
python scripts/make_test_manual.py
python -m src.ingest.ingest_pdf data/WTW5057LW0_service_manual.pdf --models WTW5057LW0
python -m src.main --ui          # http://127.0.0.1:8000
```

Hold **Space** (or hold the button) to talk; type instead if you have no speech key.

## The five beats

Each one demonstrates a specific claim. Say these in order.

**1. Identify** — click *Start the call*, or say:
> "This is technician T dash one thousand one."

Watch for: the case pulled from Salesforce, and the model number read back **character by
character** with a request for confirmation. The header fills in with model and case.

**2. Retrieve and guide** —
> "It fills but it won't agitate. What should I check first?"

Watch for: **one** step, not five. A citation on first use ("that's from the WTW5057LW0
service manual, page one"). A confirmation question at the end of the turn.

**3. Verbatim safety** —
> "I need to get into the console and check the control board."

Watch for: `The manual says, quote: WARNING: Electrical Shock Hazard. Disconnect power
before servicing.` — word for word off page 2, not paraphrased. The UI tints that message
red. The agent should wait for you to confirm the machine is unplugged before continuing.

**4. Push a diagram** —
> "Where is the capacitor? Can you show me?"

Watch for: the agent says it's pushing the figure rather than describing it, and the
schematic appears in the right-hand panel. The delivery is logged to
`data/figure_pushes.jsonl`, which is the seam a real app push replaces.

**5. Weak retrieval trips the judge** —
> "It's making a noise."

Watch for: nothing useful matches (top BM25 score ≈ 0.12), which fires the post-hook and
runs the retrieval evaluator on a separate cheaper model. It returns NEEDS_RERETRIEVAL or
ESCALATE, and the agent asks a narrowing question instead of inventing an answer. Its
exploration never enters the main conversation — only the verdict does.

**Optional — the fallback path.** Start over and say "technician T dash one thousand
forty two." No open case, so the agent asks for the model number directly and confirms it
digit by digit before searching.

## What to say when asked

- **"Is it making this up?"** — no: every instruction comes from a passage
  `manual_search` returned, and the hazard text is quoted rather than summarized. If
  nothing authoritative is retrievable for a hazardous step, it offers a transfer and
  stops rather than improvising.
- **"Could it skip the safety warning?"** — the safety rule is in a skill file the model
  reads, so a determined model could. The *structural* guarantee is narrower and worth
  stating honestly: the retrieval evaluator is fired by a code hook, not by the model's
  judgment, and it escalates any hazard question unsupported by a safety-flagged chunk.
- **"How hard is it to swap the model?"** — one env var. `LLM_PROVIDER=gemini` runs the
  same orchestrator, skills, tools and evals against Gemini on Model Garden.
- **"What's real here?"** — the real-vs-stubbed table in the README, honestly.

## Watching the machinery

```bash
tail -f observability/traces/spans.jsonl | jq -c '{name, attributes}'
```

Every tool call, model call, turn, STT and TTS call lands there. The
`hook.post_tool_use.evaluator_triggered` span is the one to point at during beat 5.
