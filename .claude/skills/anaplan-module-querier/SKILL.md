---
name: anaplan-module-querier
description: Query Anaplan module cell data via SQL with per-dimension WHERE slice XOR leaf (=, IN, or _is_leaf). Use for aggregates, rankings, and dimensional analysis across many cells.
allowed-tools:
  - aocfo_catalog_modules
  - aocfo_catalog_line_items
  - aocfo_catalog_lists
  - aocfo_sql_schema
  - aocfo_sql_query
---

# Anaplan Module Cell Data Querier Skill

Query module **cell grids** with `aocfo_sql_schema` / `aocfo_sql_query`. Use **anaplan-entity-explorer** only for member names on dimensions **that appear on the target module’s schema**. Use **anaplan-cell-explainer** for a **single** cell formula, not broad aggregates.

## Calculation hierarchy (required)

When the user asks for a **change, delta, variance, YoY comparison, or driver ranking**, follow this order. Do **not** subtract or compare numbers in your reasoning until the earlier tiers are exhausted.

### 1. Model structure first (no arithmetic)

The model may already store the answer. Before inventing a comparison:

1. **`aocfo_catalog_modules`** — prefer **REP** / **OUT** report modules at the user’s grain (see module pick table below). Search `name_contains` for *report*, *margin*, *variance*, *summary*.
2. **`aocfo_catalog_line_items`** — look for line items named **Variance**, **Delta**, **YoY**, **% Change**, **Growth**, **vs Prior**, or paired **Actual** / **Forecast** metrics on the same module.
3. **Read the structural value** with a normal slice query (`"time" = 'FY20'`, etc.) — the cell **is** the delta or the report metric; quote it, do not recompute.
4. **`aocfo_explain_cell`** when the user names one intersection and asks *why* — formula drill-down is structural, not LLM math.

| User question | Prefer structural source |
|---------------|---------------------------|
| Family × region margin (Sours + Europe) | **REP** module with **P1 Product Family** + **G2 Country** — not REV03 SKU × city |
| Single named cell | **explain_cell** or one SQL slice |
| Actual vs Forecast variance | Line item or module built for variance, else SQL (tier 2) |

### 2. SQL computation second

When no line item already holds the comparison, compute in **`aocfo_sql_query`**.

**Period change (FY19 vs FY20)** — use **one query** that returns **`margin_delta`** (or `<metric>_delta`). Each joined alias slices one period in `WHERE`. Do not narrate SQL mechanics (joins, subselects) to the user — report the numbers and drivers.

**Table alias rule (Calcite):** do **not** use reserved or keyword-like aliases — **`cur`**, **`prev`**, `current`, `value`, `time`, `join`, `select`, etc. cause **HTTP 400 Invalid or unsafe SQL**. Safe examples: **`fy20` / `fy19`**, **`t20` / `t19`**, **`actual_row` / `forecast_row`**, `a` / `b`.

**Fixed slice** (every non-time dimension constrained with `=`):

```sql
SELECT
  fy20."margin" AS margin_fy20,
  fy19."margin" AS margin_fy19,
  fy20."margin" - fy19."margin" AS margin_delta
FROM "<table>" fy20
JOIN "<table>" fy19 ON 1 = 1
WHERE fy20."time" = 'FY20'
  AND fy20."p1 product family" = 'Sours'
  AND fy20."g2 country" = 'Europe'
  AND fy19."time" = 'FY19'
  AND fy19."p1 product family" = 'Sours'
  AND fy19."g2 country" = 'Europe'
```

**Bulk leaf YoY** (many SKUs × locations) — still **one self-join query**, joining on shared non-time dimensions:

```sql
SELECT
  t20."p2 products",
  t20."g3 location",
  t20."margin" AS margin_fy20,
  t19."margin" AS margin_fy19,
  t20."margin" - t19."margin" AS margin_delta
FROM "<table>" t20
JOIN "<table>" t19
  ON t20."p2 products" = t19."p2 products"
 AND t20."g3 location" = t19."g3 location"
WHERE t20."time" = 'FY20'
  AND t19."time" = 'FY19'
  AND t20."p2 products_is_leaf" = TRUE
  AND t19."p2 products_is_leaf" = TRUE
  AND t20."g3 location_is_leaf" = TRUE
  AND t19."g3 location_is_leaf" = TRUE
ORDER BY margin_delta ASC
LIMIT 10
```

**Alternative (fixed slice only):** nested `SELECT` subqueries in the column list also work if a join is awkward — same slice rules, still one query with `margin_delta`.

**Forbidden:**

| Pattern | Why it fails |
|---------|----------------|
| Table aliases `cur`, `prev`, or SQL keywords | **Invalid or unsafe SQL** (reserved words) |
| `WHERE "time" IN ('FY19','FY20')` + `CASE WHEN` / `GROUP BY` / `PIVOT` | Dimension rule: one slice mode per query `WHERE` |
| Two separate FY queries on the same slice, then LLM subtraction | Tier 3 — use one SQL query with `margin_delta` |

#### Worked example: margin underperformance (Sours + Europe, L1MB)

User asks why margin underperformed in FY20 for Sours in Europe — **one self-join SQL call**:

```
aocfo_catalog_modules(name_contains="margin", limit=50)
aocfo_catalog_line_items(module_id="<REP01 Country Margin Report entity_long_id>")
aocfo_sql_schema(included_objects={
  "REP01 Country Margin Report": ["Margin", "Revenue", "Cost of Sales", "Margin %"]
})

aocfo_sql_query(
  query='SELECT
    fy20."margin" AS margin_fy20,
    fy19."margin" AS margin_fy19,
    fy20."margin" - fy19."margin" AS margin_delta,
    fy20."revenue" - fy19."revenue" AS revenue_delta
  FROM "l1mb.rep01 country margin report" fy20
  JOIN "l1mb.rep01 country margin report" fy19 ON 1 = 1
  WHERE fy20."time" = ''FY20'' AND fy20."p1 product family" = ''Sours'' AND fy20."g2 country" = ''Europe''
    AND fy19."time" = ''FY19'' AND fy19."p1 product family" = ''Sours'' AND fy19."g2 country" = ''Europe''',
  included_objects={
    "REP01 Country Margin Report": ["Margin", "Revenue", "Cost of Sales", "Margin %"]
  },
)
```

Use `"<table from schema>"` if the table name differs; keep **`AS margin_delta`**. Do not use aliases `cur` / `prev`.

**Other SQL-safe analysis:** rankings (`ORDER BY` / `LIMIT`), aggregates on a single time slice, explicit `"versions"` slices — still obey the WHERE contract.

### 3. LLM arithmetic last resort

Use mental math **only** when tier 1 found no structural line item and tier 2 SQL failed after a focused retry.

Do **not** subtract FY20 minus FY19 in prose when a single SQL query can return **`margin_delta`**. Formatting or rounding already-returned SQL numbers is fine; **substantive** deltas from two separate query result sets are not.

## MCP prerequisites

- Tools use the `aocfo_` prefix. Model must be bound; do **not** call `aocfo_set_model_context` unless the user asked to connect or switch.
- **`aocfo_catalog_modules`** / **`aocfo_catalog_line_items`** / **`aocfo_catalog_lists`**: pagination and filters as in entity-explorer; `module_id` and `list_id` are **`entity_long_id`** values from catalog items.
- **SQL (required):** Every `aocfo_sql_schema` and `aocfo_sql_query` call needs **`included_objects`**. Use the **same** dict for schema and query. Optional `parameters` dict for `:name` placeholders.
- **`included_objects` for modules (this skill):** Always name at least one line item per module — `{"<Module display name>": ["Line item A", ...]}`. Get names from **`aocfo_catalog_line_items`**. Include only line items you **`SELECT`** (or need as metrics); **one** line item is enough for `!tables` to expose dimension columns. **Do not** use `[]` on module keys — it pulls every line item column and is never appropriate for targeted analysis. For **lists**, **Time Model Calendar**, or **Versions**, use **anaplan-entity-explorer** (`[]` there means default entity columns, not module line items).
  - A successful `aocfo_sql_schema` does **not** carry `included_objects` forward — you must pass it again on **every** `aocfo_sql_query`.
  - If you omit `included_objects` on `aocfo_sql_query`, the MCP tool **fails validation** (`included_objects — Field required`) or the CLI **rejects** the call (or may auto-fill from the last schema in-session with a warning — still wrong; always pass it explicitly). Copy the **same** dict from your last `aocfo_sql_schema` — never send `query` alone.
  - **First-call rule:** Never invoke `aocfo_sql_schema` or `aocfo_sql_query` with empty args to “probe” — build `included_objects` from `aocfo_catalog_line_items` first, then call schema once with that map.

## Schema before clarifying questions (required)

For “what is the value / price / total of X?” questions:

1. **`aocfo_catalog_modules`** (e.g. `name_contains="price"`, `include_dimensions=true` default) → pick the module using **`list_dimensions`**, **`has_time`**, **`has_versions`**, and per-dimension **`kind`** / **`sql_note`** on each item’s **`dimensions`** array.
2. **`aocfo_catalog_line_items`** → line item names for `included_objects` ( **`applies_to`** on line items is **lists only** — Time/Versions may be missing there even when the module uses them; trust **`dimensions`** from step 1 for Time/Versions).
3. **`aocfo_sql_schema`** → read **dimension columns** on that module’s table (authoritative for SQL `WHERE`).

**Then** run `aocfo_sql_query`. Do **not** ask the user for **Time**, **Version**, or other dimensions before step 3.

| Rule | Action |
|------|--------|
| Schema has **no** `"time"` / `"versions"` | Omit those filters; **do not** ask for period or version |
| User named slice members (product, city, …) | Query with those dimensions from schema |
| User did not name a member | Use **anaplan-entity-explorer** for that list only if the dimension is on the module schema |

### Worked example: unit price (no Time, no Versions)

REV01 Price Book typically has **location + product** only (L1MB and similar models):

```
aocfo_catalog_modules(name_contains="price", limit=50)
aocfo_catalog_line_items(module_id="<entity_long_id for REV01 Price Book>")
aocfo_sql_schema(included_objects={"REV01 Price Book": ["Unit Price"]})
```

Schema shows `"g3 location"` and `"p2 products"` (no `time`, no `versions`) → query without asking for period/version:

```
aocfo_sql_query(
  query='SELECT "g3 location", "p2 products", "unit price"
    FROM "<table from schema>"
    WHERE "p2 products" = ''Nutzo Bar'' AND "g3 location" = ''New York''',
  included_objects={"REV01 Price Book": ["Unit Price"]},
)
```

## WHERE clause contract (slice **XOR** leaf)

Anaplan SQL requires **every dimension column** on the module table to be constrained in `WHERE`. For **each** dimension, choose **exactly one** mode — never both on the same dimension:

| Mode | `WHERE` pattern | When to use |
|------|-----------------|-------------|
| **Slice (one member)** | `"<dim>" = 'Entity Name'` | Single intersection, one country, one SKU, one period |
| **All leaf members** | `"<dim>_is_leaf" = TRUE` (or `= 1`) | Rank/scan/aggregate across **every leaf** on that dimension |

**XOR rule:** if you slice `"<dim>"` with `=` or `IN`, do **not** also set `"<dim>_is_leaf"` on that same dimension. Combine **different** dimensions with `AND` — each dim picks its own mode.

```sql
-- Valid (no Time/Versions on module): slice every dimension the schema lists
WHERE "p2 products" = 'Nutzo Bar' AND "g3 location" = 'New York'

-- Invalid: slice AND leaf on the same dimension
WHERE "p2 products" = 'Nutzo Bar' AND "p2 products_is_leaf" = TRUE
```

**Only when schema includes Time/Versions** — add filters for columns that exist:

```sql
WHERE "p2 products" = 'Nutzo Bar'
  AND "accounts_is_leaf" = TRUE
  AND "versions" = 'Actual'
  AND "time" = 'Jan 19'
```

Only use columns **`aocfo_sql_schema` lists** for that module (`"<dim>"` plus optional `"<dim>_is_leaf"`). Do **not** add `"time"`, `"versions"`, or their `_is_leaf` columns unless they appear on **this** module’s table.

Many modules have **no Time** and/or **no Versions** — omit those filters entirely; missing columns are not “default Actual”.

### Read dimensions from module schema first

After `aocfo_sql_schema`, list dimension columns (not line-item value columns). **Only use what is present:**

| If schema has… | Slice (preferred when you know members) | All leaves |
|----------------|----------------------------------------|------------|
| `"time"` (+ maybe `time_is_leaf`) | `"time" = 'Jan 19'` — labels from **Time Model Calendar** | `time_is_leaf = TRUE` only if you need every leaf period **and** results look correct |
| `"versions"` | `"versions" = 'Actual'` — names from **Versions** entity SQL | Avoid `versions_is_leaf` when Core misflags; use explicit slice |
| `"p2 products"` + `p2 products_is_leaf` | `"p2 products" = :m` | `"p2 products_is_leaf" = TRUE` to iterate all leaf products |
| Neither `time` nor `versions` | — | Do not filter Time/Versions |

Member strings for `=` → **anaplan-entity-explorer**. Empty rows after `_is_leaf` on Time/Versions → switch that dimension to explicit `"time"` / `"versions"` slice.

## Workflow

### 0. Pick a module (catalog)

```
aocfo_catalog_modules(name_contains="margin", limit=50)
```

Each module item includes **`dimensions`** (Time, Versions, lists) merged from the line-item **`/dimensions`** API plus **`appliesTo`** when present. **`appliesTo` alone is incomplete** — it often omits **Time** and **Versions** even though the module is time/versioned; **`has_time`** / **`has_versions`** and **`dimensions[].kind`** (`time` | `versions` | `list`) are the catalog signal before SQL.

| User grain (examples) | Prefer module whose `list_dimensions` includes… |
|-----------------------|--------------------------------------------------|
| Product **family** + **country/region** (e.g. Sours + Europe) | **P1 Product Family** + **G2 Country** (often **REP** / country report modules) |
| **SKU** + **city/location** | **P2 Products** + **G3 Location** (often **REV** / calc modules) |

DISCO heuristic (model-specific): prefer **REP** / **OUT** for reporting questions, then **CALC**, then **DATA** / **INP**. Names vary by model — use `name_contains`, not hard-coded module codes.

**Pick one module** whose **`list_dimensions`** match the user’s slice grain — do **not** query **REV03** (SKU × location) for a **family × region** question. Do **not** call `aocfo_catalog_line_items` for every module in the search result — only the module you will query.

When **`has_time`** is true, plan explicit **`"time" = 'FY20'`** slices. For **change vs prior year**, one SQL query with **`margin_delta`**; safe aliases (`fy20`/`fy19`, not `cur`/`prev`). Avoid **`time IN ('FY19','FY20')`** in one query.

Line items for that module:

```
aocfo_catalog_line_items(module_id="<entity_long_id from modules>", limit=100)
```

List dimensions on the module (optional — names for catalog cross-check):

```
aocfo_catalog_lists(name_contains="product", limit=50)
```

**Catalog** tells you whether Time/Versions exist on the module; **`aocfo_sql_schema`** still defines exact SQL column names and `_is_leaf` flags for `WHERE`.

### 1. Module schema (SQL) — source of truth for dimensions

```
aocfo_sql_schema(included_objects={"REV03 Margin Calculation": ["Revenue", "Margin %"]})
```

Scope `included_objects` to **one module** unless the user asks for a second grid (e.g. country report). Do not batch unrelated modules into schema/query — it adds columns and extra turns without helping a single-slice question.

Schema-only for dimensions? Still name line items — e.g. one placeholder from catalog is enough: `{"DEM03 Demand Forecast": ["Forecast"]}`.

Schema reveals:

- SQL `tableName` for the module (quoted in `FROM`)
- **Which dimensions exist** on this module (column names + optional `{dim}_is_leaf`)
- Line item columns

Build `WHERE` with **one XOR choice per dimension** from step 1. No `"time"` column → no Time filter.

### 2. Query module data (SQL)

**Always** pass `included_objects` on `aocfo_sql_query` — the same dict as step 1.

**Default — slice only dimensions on schema** (typical price / point lookups):

```
aocfo_sql_query(
  query='SELECT "g3 location", "p2 products", "unit price" FROM "<table>"
    WHERE "p2 products" = :prod AND "g3 location" = :loc',
  included_objects={"REV01 Price Book": ["Unit Price"]},
  parameters={"prod": "Nutzo Bar", "loc": "New York"},
)
```

#### Only when schema includes Time and/or Versions

**All explicit slices** (every dimension on schema = or IN — no `_is_leaf` on those dims):

```
aocfo_sql_query(
  query='SELECT "time", "products", "revenue" FROM "<table>"
    WHERE "versions" = :ver AND "time" = :period AND "products" = :prod
    LIMIT 20',
  included_objects={"REV03 Margin Calculation": ["Revenue"]},
  parameters={"ver": "Actual", "period": "Jan 19", "prod": "Nutzo Bar"},
)
```

**Mixed:** slice some dimensions, all leaves on others (include time/version only if on schema):

```
aocfo_sql_query(
  query='SELECT "p2 products", "accounts", "revenue" FROM "<table>"
    WHERE "p2 products" = :p1 AND "accounts_is_leaf" = TRUE
      AND "versions" = :ver AND "time" = :period
    LIMIT 50',
  included_objects={"REV03 Margin Calculation": ["Revenue"]},
  parameters={"p1": "Nutzo Bar", "ver": "Actual", "period": "Jan 19"},
)
```

**All leaf members** on every list dimension (iterate leaf entities — no `=` / `IN` on those dims):

```
aocfo_sql_query(
  query='SELECT "p3 sku", "accounts", "value" FROM "<table>"
    WHERE "p3 sku_is_leaf" = TRUE AND "accounts_is_leaf" = TRUE
    LIMIT 20',
  included_objects={"DEM03 Demand Forecast": ["Forecast"]},
)
```

Use **anaplan-entity-explorer** for member strings only for dimensions you confirmed on the module schema (or the user asked model-wide period/version list).

#### Earliest / latest week in a fiscal year

`ORDER BY "time"` on labels like `Week 5 FY20` is **lexicographic** (Week 10 sorts before Week 5). For “earliest exception in FY20”, query all matching leaf rows (`"time" LIKE '%FY20'`, `safety stock exception count` = 1, etc.) and pick the **minimum week number** in the agent answer — or use explicit week slices, not `ORDER BY time LIMIT 1`.

#### Comparing two periods (e.g. FY19 vs FY20)

Follow the **calculation hierarchy** above.

**Default:** one query with safe table aliases and **`AS margin_delta`**.

**Alternative:** nested column subqueries on a fixed slice if join fails.

Do **not** use `WHERE "time" IN ('FY19','FY20')`, aliases **`cur` / `prev`**, or LLM subtraction.

### 3. Analysis patterns

Column names are **model-specific** — take them from schema, not from examples below.

```sql
-- Ranking across all leaf members on two dimensions (slice XOR leaf: all-leaf mode on each)
SELECT "<rank_dim>", "<metric>"
FROM "<table>"
WHERE "<rank_dim>_is_leaf" = TRUE AND "<other_dim>_is_leaf" = TRUE
ORDER BY "<metric>" DESC
LIMIT 5
```

```sql
-- Top N within one country: slice country, all leaf products
SELECT "p2 products", "revenue"
FROM "<table>"
WHERE "g1 country" = 'United States' AND "p2 products_is_leaf" = TRUE
ORDER BY "revenue" DESC
LIMIT 10
```

```sql
-- Actual vs Forecast: self-join with safe aliases (not cur/prev)
SELECT
  act."<metric>" AS actual_val,
  fcst."<metric>" AS forecast_val,
  act."<metric>" - fcst."<metric>" AS variance
FROM "<table>" act
JOIN "<table>" fcst
  ON act."<shared_dim>" = fcst."<shared_dim>"
WHERE act."versions" = 'Actual'
  AND fcst."versions" = 'Forecast'
  AND act."<shared_dim>_is_leaf" = TRUE
  AND fcst."<shared_dim>_is_leaf" = TRUE
```

If a module exposes a **Variance** line item at the user’s grain, read it in tier 1 instead.

## SQL engine notes

- Apache Calcite: same-module joins, nested column subqueries, aggregates (subject to dimensional rules)
- **Table aliases:** avoid reserved words — **`cur`**, **`prev`**, and SQL keywords as aliases → HTTP 400
- **No CTEs** (`WITH`)
- Quote identifiers that contain spaces or special characters

## Error prevention

| Failure | Cause | Fix |
|--------|--------|-----|
| Validation error: `included_objects` field required | `aocfo_sql_query` called without `included_objects` (even when schema had it) | Pass `included_objects` on **every** query; reuse the same dict as `aocfo_sql_schema` |
| Missing context | No bound model | User must connect; you may read `aocfo_get_model_context` |
| SQL error on SELECT | Missing constraint on a dimension | Per dim: `=` or `"<dim>_is_leaf" = TRUE` (XOR — not both slice and leaf on same dim) |
| Redundant / confusing SQL | `"dim" = 'X' AND "dim_is_leaf" = TRUE` | Pick one mode per dimension |
| SQL error: unknown column `time` / `versions` | Module has no that dimension | Remove those filters; re-read module schema |
| Empty rows with `time_is_leaf` | Core `_is_leaf` bug (module **has** Time) | Explicit `"time" = ...` from entity-explorer calendar |
| Empty rows with `versions_is_leaf` | Core misflag (module **has** Versions) | Explicit `"versions" = ...` from entity-explorer |
| Wrong table/column | Guessed names | Re-run `aocfo_sql_schema` |
| Empty/wrong module | Wrong `included_objects` key | Key must be **module display name** from `aocfo_catalog_modules` |
| Bloated schema / vague query | `{"Some Module": []}` | Name line items from `aocfo_catalog_line_items`; never `[]` on modules |
| Asked user for Time/Version first | Guessed module needs period | Run `aocfo_sql_schema`; if no `time`/`versions` columns, query with listed dims only |
| SQL error: slice or leaf for every dimension | `time IN ('FY19','FY20')` with pivot/CASE | One query; per-alias time slices in WHERE |
| HTTP 400 Invalid or unsafe SQL | Table aliases **`cur`**, **`prev`**, or SQL keywords | Rename to `fy20`/`fy19`, `t20`/`t19`, etc. |
| Delta computed in LLM | Two FY queries on same slice | One query with `AS margin_delta` |
| Extra catalog turns | `catalog_line_items` for every search hit | Line items once for the chosen module only |
| Empty-args SQL validation error | Probed schema/query without `included_objects` | Build map from catalog line items before any SQL call |

```sql
-- Fails: no dimensional constraints
SELECT * FROM "<table>" LIMIT 10

-- Works
SELECT * FROM "<table>"
WHERE "dim_a_is_leaf" = 1 AND "dim_b_is_leaf" = 1
LIMIT 10
```

## Discovery → analysis checklist

1. `aocfo_catalog_modules` → pick module at user grain → check for report / variance line items (**tier 1**)
2. `aocfo_catalog_line_items` → pick line item names for `included_objects` (required for modules)
3. `aocfo_sql_schema` with **named** line items only → table + dimension columns (do not assume Time/Versions)
4. **anaplan-entity-explorer** only for member names on dimensions present on that schema (or model-wide period/version questions)
5. `aocfo_sql_query` — read structural metrics first; YoY/variance in one query with `margin_delta` (**tier 2**)
6. **anaplan-cell-explainer** on one intersection if the user asks *why* a value appears
7. LLM arithmetic only for bulk two-query merges or after SQL failure (**tier 3**)

## Related Skills

- **anaplan-entity-explorer** — list member names; Time/Versions only when on module
- **anaplan-cell-explainer** — one cell formula drill-down
- **anaplan-cell-writer** — updates inputs identified after analysis
