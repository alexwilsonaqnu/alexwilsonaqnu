/**
 * In-process provenance for the web path (Node only — no Python/bash subprocess).
 *
 * The file hooks (ledger-append, provenance-stop-gate) are Python/bash and fail
 * silently when a Finder-launched app has no `python3` on its PATH. So the web
 * backend does the two jobs itself, in TypeScript:
 *   1. collectFacts() — pull numbers out of an Anaplan tool RESULT into AnaplanFacts.
 *   2. verifyAnswer() — vet the finished answer's stated numbers against the ledger
 *      (the §0 backstop: a number may appear only if it traces to a fetched fact).
 *
 * Mirrors the semantics of .claude/hooks/_fpna_common.py + ledger-append.py so the
 * CLI (Python hooks) and the web app enforce the same invariant.
 */

export interface FactInput {
  value: number | string;
  label: string;
  workspaceId: string;
  modelId: string;
  module: string;
  lineItem: string;
  intersection: Record<string, string>;
  version?: string;
  source: "line_item" | "sql_calcite" | "scenario_recompute" | "explain_cell";
  query?: string;
}

const TOL = 0.005; // 0.5% relative — matches DISPLAY_TOLERANCE

// Tools whose results carry actual cell figures worth capturing (skip metadata
// like show_models / catalog, which return ids and counts, not finance numbers).
const DATA_TOOL_RE =
  /(read_cells|run_export|get_(view|list)_readrequest_page|aocfo_sql_query|aocfo_explain_cell)/;

export function isDataTool(toolName: string): boolean {
  return DATA_TOOL_RE.test(toolName);
}

// --------------------------------------------------------------------------- //
// 1. capture figures from a tool result
// --------------------------------------------------------------------------- //
const FIGURE_RE = /\$?\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\$?-?\d+\.\d+|\b\d{4,12}\b/g;

export function scanFigures(text: string): { raw: string; value: number }[] {
  const out: { raw: string; value: number }[] = [];
  const seen = new Set<string>();
  for (const m of text.matchAll(FIGURE_RE)) {
    const raw = m[0];
    const t = raw.trim();
    const neg = t.startsWith("(") && t.endsWith(")");
    const cleaned = raw.replace(/[^\d.-]/g, "");
    if (!cleaned || cleaned === "-" || cleaned === ".") continue;
    let v = Number(cleaned);
    if (!Number.isFinite(v)) continue;
    if (neg) v = -Math.abs(v);
    const key = v.toPrecision(8);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ raw, value: v });
  }
  return out;
}

function walkNumbers(node: unknown, emit: (label: string, val: number) => void, path: string[] = []): void {
  if (node == null) return;
  if (typeof node === "number") return emit(path.join(".") || "value", node);
  if (typeof node === "string") {
    const s = node.replace(/,/g, "").trim();
    if (/^[-+]?\d+(\.\d+)?$/.test(s)) emit(path.join(".") || "value", Number(s));
    return;
  }
  if (Array.isArray(node)) {
    node.forEach((v, i) => walkNumbers(v, emit, [...path, String(i)]));
    return;
  }
  if (typeof node === "object") {
    for (const [k, v] of Object.entries(node as Record<string, unknown>)) walkNumbers(v, emit, [...path, k]);
  }
}

/** Build AnaplanFacts from one Anaplan tool result (JSON grid, else text scan). */
export function collectFacts(toolName: string, input: Record<string, any>, text: string): FactInput[] {
  const base: Omit<FactInput, "value" | "label" | "lineItem" | "intersection"> = {
    workspaceId: String(input?.workspaceId ?? process.env.ANAPLAN_WS_GUID ?? ""),
    modelId: String(input?.modelId ?? process.env.ANAPLAN_MODEL_GUID ?? ""),
    module: String(input?.moduleId ?? input?.module ?? ""),
    source: toolName.includes("aocfo_sql_query") ? "sql_calcite" : toolName.includes("explain") ? "explain_cell" : "line_item",
  };
  if (input?.version) base.version = String(input.version);
  const facts: FactInput[] = [];
  const push = (value: number, label: string, lineItem: string) => {
    if (facts.length < 400) {
      facts.push({ ...base, value, label: (base.module ? base.module + " · " : "") + label, lineItem, intersection: {} });
    }
  };

  let data: unknown;
  try { data = JSON.parse(text); } catch { /* not JSON */ }
  if (data !== undefined) walkNumbers(data, (label, val) => push(val, label, label.split(".").pop() || toolName));
  if (facts.length === 0) {
    for (const { raw, value } of scanFigures(text)) push(value, raw.trim(), toolName.split("__").pop() || toolName);
  }
  return facts;
}

// --------------------------------------------------------------------------- //
// 2. vet the answer's stated numbers against the ledger
// --------------------------------------------------------------------------- //
const PLACEHOLDER = /\{\{\s*fact:[^}]*\}\}/g;
const ISO_DATE = /\b\d{4}-\d{2}-\d{2}\b/g;
const IDENT = /\b(?:FY|Q|H|CY|P)\s?\d{1,4}\b/gi;
const NUM = String.raw`(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)`;
const TOKEN = new RegExp(
  `(?<cur>[$£€]\\s?${NUM}\\s?(?:[KkMmBb]n?|bn|mm)?)` +
    `|(?<pct>${NUM}\\s?%)` +
    `|(?<mult>${NUM}\\s?[x×])` +
    `|(?<bare>${NUM}(?:\\s?(?:[KkMmBb]n?|bn|mm))?)`,
  "g",
);

function toNumber(raw: string): number {
  const low = raw.toLowerCase();
  let mult = 1;
  if (/\bbn\b|bn$/.test(low) || /\d\s*b\b/.test(low)) mult = 1e9;
  else if (/\bmm\b|mm$|\d\s*m\b/.test(low)) mult = 1e6;
  else if (/\d\s*k\b/.test(low)) mult = 1e3;
  const cleaned = raw.replace(/[^\d.-]/g, "");
  const n = Number(cleaned);
  return Number.isFinite(n) ? n * mult : NaN;
}

export function statedNumbers(text: string): number[] {
  const masked = text.replace(PLACEHOLDER, " ").replace(ISO_DATE, " ").replace(IDENT, " ");
  const out: number[] = [];
  for (const m of masked.matchAll(TOKEN)) {
    const g = m.groups ?? {};
    const val = toNumber(m[0]);
    if (!Number.isFinite(val)) continue;
    const isBare = g.bare !== undefined && g.cur === undefined && g.pct === undefined && g.mult === undefined;
    if (isBare) {
      const iv = Math.abs(val);
      if (Number.isInteger(iv) && (iv <= 12 || (iv >= 1900 && iv <= 2099))) continue;
    }
    out.push(val);
  }
  return out;
}

function approxEqual(a: number, b: number): boolean {
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
  if (a === b) return true;
  const scale = Math.max(Math.abs(a), Math.abs(b), 1e-9);
  return Math.abs(a - b) / scale <= TOL;
}

/** Every stated number must trace to a ledger value (incl. percent↔fraction). */
export function verifyAnswer(answer: string, factValues: number[]): { ok: boolean; unsourced: number[] } {
  const seen = new Set<string>();
  const unsourced: number[] = [];
  for (const n of statedNumbers(answer)) {
    const key = n.toPrecision(8);
    if (seen.has(key)) continue;
    seen.add(key);
    const sourced = factValues.some((f) => approxEqual(n, f) || approxEqual(n, f * 100) || approxEqual(n, f / 100));
    if (!sourced) unsourced.push(n);
  }
  return { ok: unsourced.length === 0, unsourced };
}
