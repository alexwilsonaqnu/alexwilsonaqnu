---
name: anaplan-cell-explainer
description: Explain how one Anaplan cell value is calculated (formula drill-down). Call explain_cell directly when the user names module, line item, and members; use SQL/catalog only to discover an unknown intersection.
allowed-tools:
  - aocfo_explain_cell
  - aocfo_catalog_modules
  - aocfo_catalog_line_items
  - aocfo_catalog_lists
  - aocfo_sql_schema
  - aocfo_sql_query
---

# Anaplan Cell Explainer Skill

Decompose **one cell** with `aocfo_explain_cell`. For many cells or rankings, use **anaplan-module-querier**. For writes, use **anaplan-cell-writer** after you trace inputs.

## MCP prerequisites

- Do **not** call `aocfo_set_model_context` unless the user asked to connect or switch.
- Names must match catalog/SQL exactly: **module display name**, **line item name**, list dimension names, member names.
- SQL calls require **`included_objects`** on both `aocfo_sql_schema` and `aocfo_sql_query` (same dict as module-querier).

## When to Use This Skill

- User asks *why* a number is what it is (one intersection)
- Tracing calculated line items back to **input** modules before `aocfo_cell_write`
- Following `components` / `next_level` for deeper drill-down

## Fast path — user named module, line item, and intersection

When the user already gives **module**, **line item**, and dimension members (product, location, time, …), call **`aocfo_explain_cell` immediately** after loading this skill. Do **not** load **anaplan-module-querier**, run SQL, or catalog discovery first.

Map informal names to **list display names** on the module (from catalogs or a prior failed explain — not guessed country/region lists):

| User says | L1MB `REV03 Margin Calculation` pages keys |
|-----------|---------------------------------------------|
| product Sours | `P2 Products`: `Sours` |
| location Europe | `G3 Location`: `Europe` (not `G2 Country`) |
| time FY20 | `Time`: `FY20` |

Worked example (L1MB — matches successful explain):

```
aocfo_explain_cell(
  module="REV03 Margin Calculation",
  line_item="Margin %",
  pages={"P2 Products": "Sours", "G3 Location": "Europe", "Time": "FY20"},
)
```

Use SQL or `aocfo_catalog_lists` only when explain returns **dimension not found** or the user did not name the intersection.

## Two-step pattern (discovery only)

### Step 1 — Find the cell (module-querier SQL)

Examples below assume a module **with** Time, Versions, and lists — omit `time` / `versions` from SQL and `pages` when the module schema has no those columns.

```
aocfo_sql_schema(included_objects={"REP01 Country Margin Report": ["Margin"]})

aocfo_sql_query(
  query='SELECT "country", "product", "margin" FROM "<table>" WHERE "time_is_leaf" = 1 AND "country_is_leaf" = 1 AND "product_is_leaf" = 1 AND "versions" = :ver ORDER BY "margin" ASC LIMIT 5',
  included_objects={"REP01 Country Margin Report": ["Margin"]},
  parameters={"ver": "Actual"},
)
```

Pick one row: module name, line item, and each dimension member.

### Step 2 — Explain that cell

```
aocfo_explain_cell(
  module="REP01 Country Margin Report",
  line_item="Margin",
  column={"list": "Products", "member": "Nutzo Bar"},
  pages={"Country": "US", "Time": "Jan 19", "Versions": "Actual"},
)
```

## Parameters

| Parameter | Role |
|-----------|------|
| `module` | Module display name |
| `line_item` | Line item display name |
| `pages` | Page dimensions: `{"ListName": "MemberName", ...}` |
| `column` | Optional column list: `{"list": "<ListName>", "member": "<MemberName>"}` |
| `row` | Optional row list: same shape as `column` when line items are on rows |
| `layout` | `auto` (default), `line_item_on_rows`, or `line_item_on_columns` |
| `drill_down_id` | From prior response `next_level` — deeper drill on one component |
| `drill_down_level` | Level index (default `0`) |
| `include_raw_payload` | `true` only when debugging (large payload) |

### Layout hints

- Products on **columns**, Time on **columns**, locations on **pages**: put Time in `row` or `column` and products/locations in `pages` + `column` per MCP tool description.
- If drill-down fails, try an explicit `layout` or swap `row` / `column` / `pages` using module schema.

Validate member names with **anaplan-entity-explorer** or catalogs when uncertain. Include **Time** / **Versions** in `pages` / `column` / `row` only if that module uses those dimensions (from schema or explain tool docs).

## Response shape

- **`summary`**: Title and messages
- **`components`**: Formula drivers (operands, references)
- **`next_level`**: Use `drill_down_id` / `drill_down_level` for deeper drill on a component

## Progressive drill-down

1. `aocfo_explain_cell` on the target cell
2. Read `components` (e.g. Revenue, COGS, Volumes)
3. If a driver is unclear, explain **that driver's cell** at the same intersection (or use `drill_down_id` when provided)
4. Stop at **input** line items (INP/DATA-style modules) — that is the write target for cell-writer

## Workflow with cell writer

```
SQL → lowest margin intersection
  → aocfo_explain_cell(Margin)
  → components: Revenue = Volumes × Unit Price
  → aocfo_explain_cell(Volumes) → REV02 Volume Inputs
  → aocfo_cell_write on input module (user confirmed)
  → aocfo_sql_query to verify downstream Margin
```

## Best Practices

1. **One cell at a time** — not a substitute for aggregates
2. **Exact names** — use `aocfo_catalog_modules`, `aocfo_catalog_line_items`, `aocfo_catalog_lists` when unsure
3. **Explain first** when the user named module, line item, and members; **SQL first** only when the intersection is unknown
4. Confirm before **anaplan-cell-writer**

## Common mistakes

| Mistake | Fix |
|--------|-----|
| SQL/catalog before explain when user named the cell | Call `aocfo_explain_cell` with mapped list names first |
| Wrong list on pages (e.g. `G2 Country` for Europe on REV03) | Use module applies-to lists (`G3 Location`, `P2 Products`, `Time` on L1MB REV03) |
| Explain before picking a cell | Run module-querier SQL first **only** when intersection unknown |
| Wrong `pages` / `column` / `row` | Cross-check catalogs and entity-explorer |
| Explain whole module | Use SQL with leaf filters |
| Write to calculated line item | Drill to input module via explain chain |
| SQL without `included_objects` | Always pass `included_objects` on schema and query |

## Related Skills

- **anaplan-module-querier** — find which cell to explain
- **anaplan-entity-explorer** — Time, Versions, and list member names for `pages` / `column` / `row`
- **anaplan-cell-writer** — change inputs found via explain
