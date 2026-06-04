---
description: Variance analysis at a grain — actual vs plan/forecast/prior, with bridge, ranked drivers, and sourced commentary.
argument-hint: <grain, e.g. "EMEA gross margin Q3 FY26 vs plan">
---

Run the **Variance Analysis** capability (§8.1) for: **$ARGUMENTS**

Pipeline (prompt chaining, every number provenanced):

1. **Retrieve** — delegate to `anaplan-retriever`. Walk the calculation hierarchy:
   - Tier 1: find a REP/OUT module with a stored *Variance/Delta/vs Plan* line item at this grain and read it.
   - Tier 2: if none, request ONE Calcite query returning the variance `AS variance` (engine computes).
   Capture actual, the comparison base (plan/forecast/prior), and the variance — all as `AnaplanFact[]` in the ledger.

2. **Analyze** — delegate to `variance-analyst`: build the plan→actual **bridge** and a
   **driver-contribution ranking** ordered by magnitude. Ordering/selecting facts is allowed;
   differencing them is not — request any `*_delta` from the retriever.

3. **Narrate** — delegate to `narrative-synthesizer`: first-pass commentary in the CFO register.
   Every number is a `{{fact:<requestId>}}` placeholder, never a typed numeral.

4. **Evaluate** — the `provenance-stop-gate` (hard gate: 100% of numbers sourced) plus
   `provenance-evaluator` (faithfulness/completeness/craft) must pass before release.

5. **Deliver** to the conversational interface, resolving placeholders against the ledger.

Do not compute any variance yourself. If you are tempted to type a number, stop and have the
retriever fetch it.
