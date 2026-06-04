---
name: anaplan-cell-writer
description: Write values to Anaplan module cells via aocfo_cell_write. Use after catalogs validate module, line item, and dimension members; confirm with the user before writing.
allowed-tools:
  - aocfo_catalog_modules
  - aocfo_catalog_line_items
  - aocfo_catalog_lists
  - aocfo_cell_write
  - aocfo_sql_schema
  - aocfo_sql_query
---

# Anaplan Cell Writer Skill

Write cell values with **`aocfo_cell_write`**. Dependent calculations update in the model. **Confirm with the user** before any write.

## MCP prerequisites

- Do **not** call `aocfo_set_model_context` unless the user asked to connect or switch.
- **`module`**: module **display name** (from `aocfo_catalog_modules`).
- **`writes`**: list of up to **1000** objects per call; all writes must be in the **same** module.
- Each write: `line_item` (or `line_item_id`), `value`, and `dimensions` — either `{"Dimension Name": "Member Name", ...}` or `[{"dimension_id": "...", "item_id": "..."}, ...]`.
- Optional: `poll_model_open_first` (default true), `include_raw_response` (default false).
- Verification SQL uses the same **`included_objects`** rules as module-querier.

## When to Use This Skill

- Set or update a specific input/assumption cell
- Reversible what-if on inputs (capture baseline → write → verify → restore)
- After analysis identified the correct **input** module and intersection

> Prefer **input** modules (INP, DATA, override modules). Calculated line items cannot be written — use **anaplan-cell-explainer** to find the source input.

## Workflow: discover → confirm → write → verify

### Step 0 — Validate target (required)

```
aocfo_catalog_modules(name_contains="Volume", limit=50)

aocfo_catalog_line_items(module_id="<entity_long_id>", name_contains="Volume", limit=50)
```

Use **anaplan-entity-explorer** for exact list (and Time/Versions **only if applicable**) member names in `dimensions`. Omit Time/Versions keys when the module has no those dimensions.

### Step 1 — Trace drivers (optional)

Load **anaplan-cell-explainer** when the target is a calculated value:

```
aocfo_explain_cell(
  module="REV03 Margin Calculation",
  line_item="Revenue",
  column={"list": "P2 Products", "member": "Nutzo Bar"},
  pages={"G3 Location": "New York", "Time": "Jan 19"},
)
```

### Step 2 — Baseline (SQL)

```
aocfo_sql_schema(included_objects={"REV02 Volume Inputs": ["Volumes"]})

aocfo_sql_query(
  query='SELECT "volumes" FROM "<table>" WHERE "g3 location" = :loc AND "p2 products" = :prod AND "time" = :t',
  included_objects={"REV02 Volume Inputs": ["Volumes"]},
  parameters={"loc": "New York", "prod": "Nutzo Bar", "t": "Jan 19"},
)
```

Column names come from schema — adjust to match the model.

### Step 3 — Write

Single write:

```
aocfo_cell_write(
  module="REV02 Volume Inputs",
  writes=[
    {
      "line_item": "Volumes",
      "value": 261,
      "dimensions": {
        "G3 Location": "New York",
        "P2 Products": "Nutzo Bar",
        "Time": "Jan 19"
      }
    }
  ],
)
```

Batch (same module only):

```
aocfo_cell_write(
  module="Module Name",
  writes=[
    {"line_item": "Flag A", "value": true, "dimensions": {...}},
    {"line_item": "Value B", "value": "900", "dimensions": {...}},
  ],
)
```

If Core returns `unsupportedCellDataType`, retry with a string `value`.

### Step 4 — Verify downstream (SQL)

Re-query affected calculated cells with module-querier patterns and the same dimensional rules.

## Override / state-machine models

Some models require ordered steps — **do not** batch dependent flags and values in one call when order matters:

1. Trigger default / submit flag (if applicable)
2. Enable override flag
3. Write override value
4. Read final / variance line items
5. Restore baselines for reversible what-if

Prefer a **Scenario / Version** dimension when writable instead of mutating production Actuals.

### INV01 Inventory Ordering — shipping override what-if (L2MB)

For `Submit Purchase Order Request` + shipping overrides at one Time × P3 SKU intersection, use **one line item per `aocfo_cell_write` call** in this order:

1. `Submit Purchase Order Request` = `true`
2. `Override Shipping Method` = `true`
3. `Override Method` = `"Road"` (string)
4. `aocfo_sql_query` — read final shipment, method, lead time, cost, variance
5. Restore separately: `Submit Purchase Order Request` = `false`, `Override Shipping Method` = `false`, `Override Method` = `""`

**Never batch** steps 1–3 in a single `writes` array — batched PO + override calls often return `unsupportedCellDataType` even when sequential single-cell writes succeed. If a write fails, retry that **one** cell with a string value (`"true"` / `"Road"`) before moving on.

## Response

```json
{
  "success": true,
  "message": "OK",
  "updated_count": 1,
  "failed_count": 0,
  "failures": []
}
```

Non-empty **`failures`** — inspect per-cell errors; fix names, dimensions, or data type.

## Common mistakes

| Mistake | Fix |
|--------|-----|
| Writing calculated line item | Explain chain → input module |
| Wrong member spelling | anaplan-entity-explorer |
| Missing dimension in `dimensions` | Full set from module schema |
| SQL verify without `included_objects` | Pass `included_objects` on schema and query |
| Batched override steps | Sequential `aocfo_cell_write` calls |

## Related Skills

- **anaplan-entity-explorer** — Time, Versions, and list member names
- **anaplan-cell-explainer** — find writable input for a calculated cell
- **anaplan-module-querier** — baseline and impact verification
