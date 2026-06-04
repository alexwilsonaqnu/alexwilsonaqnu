---
name: anaplan-variance-bridge
description: >-
  Source a variance structurally and build the plan→actual walk (bridge) plus a
  driver-contribution ranking. Use for variance analysis, actual-vs-plan/forecast/
  prior questions, "why did X move", and bridge/waterfall narratives. The bridge
  steps are engine-computed, never differenced by the model.
---

# Anaplan Variance Bridge

## Source the variance — walk the calculation hierarchy (do NOT subtract)
1. **Tier 1 — structure first.** `aocfo_catalog_modules` → prefer REP/OUT report modules at the
   grain. `aocfo_catalog_line_items` → find a line item literally named *Variance / Delta / vs
   Plan / vs Forecast / vs Prior / YoY / % Change / Growth / Contribution*. Read it. The cell IS
   the variance — quote it.
2. **Tier 2 — Calcite computes it.** No stored variance? Emit ONE query that returns it:
   ```sql
   SELECT a."revenue" AS actual,
          p."revenue" AS plan,
          a."revenue" - p."revenue" AS variance
   FROM "<table>" a JOIN "<table>" p ON 1=1
   WHERE a."version" = 'Actual' AND p."version" = 'Budget'
     AND a."time" = 'Q3 FY26' AND p."time" = 'Q3 FY26'
     AND a."g2_country" = 'Europe' AND p."g2_country" = 'Europe'
   ```
   Aliases `a`/`p` (or `fy26`/`fy25`) — **never `cur`/`prev`**. `included_objects` on every call.
   slice-XOR-leaf per dimension. No CTEs. The subtraction happens in Anaplan.

## Build the bridge (ordering is allowed, differencing is not)
A bridge walks Base → +/− driver steps → Final. Each step's magnitude is a **fact**:
- If the model stores driver-level variance line items (contribution by driver), read each — done.
- Otherwise request one Calcite query per driver step that returns the step `AS step_<driver>`.
- Order the steps by magnitude (largest absolute contribution first). **You may sort and select
  facts; you may not difference them.** If you need Base→Final to reconcile, request the
  reconciling residual `AS bridge_residual` from the engine — do not compute it.

## Driver-contribution ranking
Rank drivers by the magnitude of their (engine-computed) contribution fact, descending. Carry each
driver's `AnaplanFact` (with intersection + query) into the output so the narrative can reference
`{{fact:<requestId>}}` and the board appendix can cite it.

## Output (consumed by narrative-synthesizer / board-deck-builder)
```json
{
  "base": { "label": "...", "fact": "<requestId>" },
  "steps": [ { "driver": "Volume", "fact": "<requestId>", "direction": "+" } ],
  "final": { "label": "...", "fact": "<requestId>" },
  "drivers_ranked": [ { "driver": "FX", "fact": "<requestId>" } ]
}
```
Never emit numerals here — only fact references. The numbers live in the ledger.
