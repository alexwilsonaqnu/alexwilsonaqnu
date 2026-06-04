---
name: numeric-provenance
description: >-
  The AnaplanFact contract and output rules. Use whenever you surface, narrate,
  format, or assemble a number from Anaplan — every figure must travel as a
  provenanced fact and trace back to its exact intersection. Triggers on
  variance, forecast, KPI, board, briefing, or any task that emits a numeral.
---

# Numeric Provenance

## The rule (§0, non-negotiable)
A number may appear in output **if and only if** it exists in the run-scoped provenance ledger
(`.fpna/ledger/<session>.jsonl`) as an `AnaplanFact`. No ledger entry → no mention. This is
enforced by the `provenance-stop-gate` (Stop hook) and `no-math-gate` (PreToolUse), not trusted.

## The AnaplanFact (defined in `src/ledger/fact.ts`)
```ts
interface AnaplanFact {
  value: number | string;        // exactly as Anaplan returned it — IMMUTABLE
  label: string;                 // "EMEA Q3 Revenue variance to plan"
  workspaceId: string; modelId: string;
  module: string; lineItem: string;
  intersection: Record<string,string>; // {"G2 Country":"Europe","Time":"Q3 FY26"}
  version?: string;              // "Actual" | "Forecast" | "Budget" | scenario name
  source: "line_item" | "sql_calcite" | "scenario_recompute" | "explain_cell";
  query?: string;                // exact SQL when source = sql_calcite
  requestId: string;             // correlates ledger ↔ OTel span
  fetchedAt: string;             // ISO timestamp
}
```

## Rules of use
- **`value` is immutable after retrieval.** Rounding/formatting for display is allowed and is
  *presentation*, not a new fact — never store a reformatted value back as a fact.
- **Derived comparisons are facts too** (variance, %, YoY, growth, contribution): `source:
  "sql_calcite"` with the originating `query` attached, because Calcite computed them.
- **Reforecast / sensitivity outputs** are facts with `source: "scenario_recompute"` and the
  `version` they were read from.
- **Never type a numeral in narrative.** Emit `{{fact:<requestId>}}` placeholders; the assembler
  resolves them against the ledger. If you want to state a number that isn't in the ledger, you
  must ask the orchestrator to have the retriever fetch it first.

## What counts as "making a number" (forbidden)
Summing, differencing, multiplying, dividing, ratioing, annualizing, extrapolating, interpolating,
or otherwise computing a substantive value outside Anaplan — in prose, Python, pandas/numpy, or a
shell. Formatting a single already-fetched value (currency, %, rounding) is fine.

## The escape hatch (always available)
Need a number you don't have? Don't compute it — **ask the retriever**: either read a structural
line item that holds it, or request one Calcite query that returns it `AS <alias>`.
