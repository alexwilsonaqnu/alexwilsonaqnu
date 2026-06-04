# Provenance evals (§13)

Gold question→fact sets. The **hard metric**, non-negotiable:

- **100%** of output numbers trace to a ledger fact, and
- **0** LLM-computed deltas in the trace.

Anything less is a **failing build**, not a quality nit. The `provenance-stop-gate`
(Stop hook) and `no-math-gate` (PreToolUse) are the runtime enforcers; these evals are
the regression net.

## Format
Each `*.json` case:
```json
{
  "id": "pe01",
  "question": "EMEA revenue variance to plan, Q3 FY26",
  "ledger": [ /* AnaplanFact[] that the retriever is expected to land */ ],
  "answer": "EMEA revenue was {{fact:req_a}} versus a plan of {{fact:req_b}}, a variance of {{fact:req_c}}.",
  "expect": { "all_numbers_sourced": true, "llm_computed_deltas": 0 }
}
```

## Running
The stop-gate is the scorer for `all_numbers_sourced`: feed `answer` as the draft and
`ledger` as the session ledger; a passing case exits 0, a violation exits 2. See
`tests/hooks/provenance-stop-gate.test.mjs` for the harness pattern.

## `trace-replays/`
Captured real runs (transcript + ledger) for regression — replay them through the
stop-gate and the evaluator after any skill/hook change.
