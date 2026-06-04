/**
 * The Numeric Provenance Contract (§3).
 *
 * Every numeric value the system surfaces travels as a provenanced fact, not a
 * bare number. A number may appear in output if and only if it exists in the
 * run-scoped ledger as one of these. This is enforced by hooks (§7), not trusted.
 */

export type FactSource =
  | "line_item" // read straight from an Anaplan line item (Tier 1)
  | "sql_calcite" // computed by Anaplan's Calcite engine in one query (Tier 2)
  | "scenario_recompute" // read back after a scenario/forecast recompute (§8.2/8.3)
  | "explain_cell"; // structural drill of a cell's formula (not arithmetic)

export interface AnaplanFact {
  /** Exactly as Anaplan returned it. Immutable after retrieval. */
  value: number | string;
  /** Human label, e.g. "EMEA Q3 Revenue variance to plan". */
  label: string;
  workspaceId: string;
  modelId: string;
  /** Module display name. */
  module: string;
  /** Line item display name, or the derived alias for SQL-computed values. */
  lineItem: string;
  /** The exact intersection, e.g. {"G2 Country":"Europe","Time":"Q3 FY26"}. */
  intersection: Record<string, string>;
  /** "Actual" | "Forecast" | "Budget" | scenario name. */
  version?: string;
  source: FactSource;
  /** The exact SQL, if source === "sql_calcite". */
  query?: string;
  /** Correlates to the ledger line + the OTel span. */
  requestId: string;
  /** ISO timestamp. */
  fetchedAt: string;
}

/** Tolerance used when matching an output token to a ledger value for display. */
export const DISPLAY_TOLERANCE = 0.005; // 0.5% relative, see provenance-stop-gate

/** A derived comparison is itself a fact (source must be sql_calcite). */
export function isDerivedComparison(f: AnaplanFact): boolean {
  return f.source === "sql_calcite" && typeof f.query === "string";
}

/**
 * Numeric value of a fact for matching/equality. Strings that aren't numeric
 * return NaN (they are treated as labels, not numbers, by the stop-gate).
 */
export function factNumber(f: AnaplanFact): number {
  if (typeof f.value === "number") return f.value;
  const cleaned = f.value.replace(/[\s,$%x×]/g, "");
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : NaN;
}
