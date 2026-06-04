---
name: anaplan-entity-explorer
description: Discover member names for list dimensions, Time Model Calendar, or Versions when those apply. Use before module SQL or writes only for dimensions present on the target module (or when the user asks model-wide period/version lists).
allowed-tools:
  - aocfo_catalog_lists
  - aocfo_catalog_properties
  - aocfo_sql_schema
  - aocfo_sql_query
---

# Anaplan Entity Explorer Skill

Discover **dimension member names** for **lists**, **native Time**, and **Versions** — only for dimensions that appear on the **target module’s** `aocfo_sql_schema`, or when the user asks model-wide period/version lists. **`aocfo_catalog_modules`** `dimensions` / `has_time` / `has_versions` show whether Time/Versions apply (line-item **`applies_to`** may omit them). Do **not** use this skill as a reason to ask for Time or Version before module schema is known. Not every module uses Time or Versions; check catalog dimensions then schema.

## Entity types

| Entity | In `aocfo_catalog_lists`? | `included_objects` key | Typical SQL `tableName` (from schema) |
|--------|---------------------------|------------------------|--------------------------------------|
| **List** | Yes | List display name, e.g. `"Products"` | `{model}.{list}` — columns vary; read schema |
| **Time** | No | **`"Time Model Calendar"`** (exact) | e.g. `{model}.time model calendar` |
| **Versions** | No | **`"Versions"`** (exact) | e.g. `{model}.versions` |
| **Module** | N/A (use module-querier) | Module display name | Module grid table — not for member enumeration |

**Time** and **Versions** never appear in `aocfo_catalog_lists`. Do not guess their table names.

## MCP prerequisites

- Tools are prefixed with `aocfo_`. Model must be bound; do **not** call `aocfo_set_model_context` unless the user asked to connect or switch.
- **`aocfo_sql_schema`** runs Core `!tables`. It returns **structure only** (`results.schema.tables`: `tableName`, `columns`, …) — not member rows.
- **`included_objects`** is required on every `aocfo_sql_schema` and `aocfo_sql_query` call. Use the **same** map on both.
- Keys are **display names** for **lists**, **`"Time Model Calendar"`**, or **`"Versions"`** — not modules (use **anaplan-module-querier** for module grids).
- **Values for entities in this skill:** `[]` means default entity columns (member fields) without naming list **properties** — appropriate for lists, Time, and Versions. **Do not** put module keys here with `[]`; module line items must be named via `aocfo_catalog_line_items` in module-querier.
- **`aocfo_catalog_properties`** is list **property** metadata, not list members.

## Critical: `!tables` is scoped by `included_objects`

A module-only schema call returns **only that module table** — not Time, Versions, or lists.

Batch schema only for **entities** you **need** (lists / Time / Versions — not modules):

```
aocfo_sql_schema(included_objects={
  "Products": [],
})
```

Add `"Time Model Calendar": []` and/or `"Versions": []` only when the target module schema includes `"time"` / `"versions"`, or the user asked for model-wide periods or versions. Module schema and cell queries belong in **anaplan-module-querier** with **named** line items.

Parse `results.schema.tables[]`:

- **`tableName`** → use quoted in `FROM "..."` on queries
- **`columns`** → `columnName` per entity; **do not assume** list columns (`_name`, `_is_leaf`) on Time or Versions

## When to use this skill

- **List members** — module schema shows a list dimension column; need exact spelling for `WHERE` or `dimensions`
- **Time** — module schema includes a `"time"` (or equivalent) column, or user asks which periods exist in the model
- **Versions** — module schema includes `"versions"`, or user asks which versions exist in the model
- Module query empty on `time_is_leaf` / `versions_is_leaf` **and** that column exists on the module — resolve member names here

**Do not use** Time Model Calendar / Versions SQL for a module that has no Time/Versions on its schema (e.g. many SYS, export, or assumption modules).

## Workflow

### 1. Lists — catalog then SQL

```
aocfo_catalog_lists(name_contains="product", limit=50)
```

Use exact list **name** from items as the `included_objects` key and for `pages` / `dimensions` labels.

Optional properties (not members):

```
aocfo_catalog_properties(list_id="<entity_long_id>", limit=50)
```

Include the list in a batched `aocfo_sql_schema` (step 0 above), then query members using **columns from schema** (often `_name`, `_code`, `parent`, `_is_leaf` — verify per list).

### 2. Versions — schema then SQL (only if module has Versions or user asked model-wide)

**Schema** (fixed key):

```
aocfo_sql_schema(included_objects={"Versions": []})
```

**Members** (column names from schema — commonly `_name`):

```
aocfo_sql_query(
  query='SELECT "_name" FROM "<table_from_schema>" LIMIT 50',
  included_objects={"Versions": []},
)
```

Use exact strings on module grids: `WHERE "versions" = 'Actual'` (column name from **module** schema, usually lowercase `"versions"`).

### 3. Time — schema then SQL (only if module has Time or user asked model-wide)

**Schema** (fixed key):

```
aocfo_sql_schema(included_objects={"Time Model Calendar": []})
```

**Members** — read columns from schema. Typical pattern:

- **`label`** — period text used in module `WHERE "time" = ...` (e.g. `Jan 19`, `Week 1 FY19`)
- **`parent`** — hierarchy parent period
- **`_levels`** — period **type** string (`week`, `month`, …) — **not** a boolean leaf flag

```sql
-- Example: month-level periods (adjust _levels to the grain the user needs)
SELECT "label", "parent", "_levels"
FROM "<time_table_from_schema>"
WHERE "_levels" = 'month'
LIMIT 50
```

Do **not** filter `"_levels" = 1` — it is a string and will error.

### 4. Handoff to module-querier

Apply only for dimensions **present on the module schema**:

| Resolved here | Use on module SQL (if column exists) |
|---------------|--------------------------------------|
| Version `_name` | `"versions" = :ver` |
| Time `label` | `"time" = :period` or `"time" IN (...)` |
| List member name | `"<list_column>" = :member` |

If the module schema has no `"versions"` / `"time"` column, do not pass Versions/Time in module `WHERE` or explain/write `pages`.

## Core `_is_leaf` bug (only when module schema has Time/Versions)

Applies only if the module table includes `time_is_leaf` / `versions_is_leaf` (or `"time"` / `"versions"` columns). Core can misflag leaf semantics on those dimensions. Entity tables are the source of truth for **member names**; module `_is_leaf` flags are mainly for SQL slice-or-leaf compliance.

**Prefer:**

1. **Explicit slice** — `"versions" = 'Actual'`, `"time" = 'Jan 19'` (satisfies slice-or-leaf without `_is_leaf` on those dims)
2. **Names from this skill** — build `IN (...)` from Time Model Calendar / Versions SQL
3. **`_is_leaf` on list dimensions** only when list entity SQL agrees

**If** `time_is_leaf = 1` returns empty but periods exist in Time Model Calendar → drop `time_is_leaf`, filter by explicit `"time"` values from calendar SQL.

**Do not** copy list `_is_leaf` patterns onto Time Model Calendar (`_levels` is not `_is_leaf`).

## Error prevention

| Failure | Cause | Fix |
|--------|--------|-----|
| Queried Time/Versions entity for wrong module | Module has no `time`/`versions` columns | Skip sections 2–3; use list workflow only |
| Wrong column on Time | Assumed `_name` | Use `"label"` (or whatever schema shows) |
| SQL type error on `_levels` | Treated as numeric leaf | Compare to string: `"_levels" = 'week'` |
| Empty module query | Wrong `time_is_leaf` | Explicit `"time"` / `"versions"` from entity SQL |
| Guessed table name | Skipped schema | Always read `tableName` from `aocfo_sql_schema` |

## Best practices

1. **Batch `aocfo_sql_schema`** — lists (and Time/Versions when relevant) with `[]`; module schema stays in module-querier with named line items
2. **Schema before every entity query** — same `included_objects` on schema and query
3. **`LIMIT`** on exploration queries
4. **Catalog lists** for list dimension names; fixed keys for Time/Versions
5. For list members when no list table in schema → module-querier `DISTINCT` only as last resort (not for Time/Versions)

## Related skills

- **anaplan-module-querier** — module cell SQL; per-dimension `WHERE` is slice (`=` / `IN`) **XOR** `<dim>_is_leaf = TRUE`
- **anaplan-cell-explainer** / **anaplan-cell-writer** — one cell / writes; need exact `pages` and `dimensions` member names
