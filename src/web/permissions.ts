/**
 * Read-only tool policy for the live web session (Sprint 0 / Phase 1).
 *
 * The web app drives the orchestrator headlessly — there is no human to answer
 * an "ask" prompt mid-stream — so we decide tool access up front:
 *   • ALLOW the orchestration + retrieval read surface.
 *   • DENY every write/import/process/delete (those are Phase 2, gated, and
 *     additionally blocked by scenario-write-guard).
 * The no-math gate and provenance Stop-gate (file hooks) still run on top of this.
 */

/** Anaplan Chimera read/explore tools the lead needs. */
const CHIMERA_READ = [
  "aocfo_catalog_modules",
  "aocfo_catalog_line_items",
  "aocfo_catalog_lists",
  "aocfo_catalog_properties",
  "aocfo_sql_schema",
  "aocfo_sql_query",
  "aocfo_explain_cell",
  "aocfo_get_model_context",
  // binds which workspace/model to read — needed when the connection headers
  // don't auto-bind (get_model_context returns null). Selects a model to READ;
  // it mutates no data, so it's allowed in the read path.
  "aocfo_set_model_context",
];

/**
 * Orchestration / context tools. NOTE: no `Task` — in the web path the lead
 * retrieves directly via the Anaplan tools so that every raw MCP response is
 * captured by the ledger-append hook. A subagent only returns a text summary,
 * whose underlying responses never reach the ledger, so its numbers can't be
 * verified. (Subagent isolation, §5, is a full-system optimization, not needed
 * for the invariant, which the hooks enforce regardless.)
 */
const ORCHESTRATION = ["Skill", "TodoWrite", "Read", "Grep", "Glob"];

export const ALLOWED_TOOLS = [
  ...ORCHESTRATION,
  ...CHIMERA_READ.map((t) => `mcp__anaplan-chimera__${t}`),
];

/** Anything that mutates data: denied in the web read path. */
export const DISALLOWED_TOOLS = [
  "mcp__anaplan-chimera__aocfo_cell_write",
];

// set_model_context is intentionally NOT matched here (it binds a model to read,
// not a data mutation); the dangerous set_* ops are caught explicitly.
const WRITE_RE = /(write|import|process|delete|close_model|set_currentperiod|set_fiscalyear|reset_index)/i;

type Decision =
  | { behavior: "allow"; updatedInput: Record<string, unknown> }
  | { behavior: "deny"; message: string };

import { OPS_READ_RE, OPS_DENY_RE } from "../mcp/ops.js";

/**
 * canUseTool backstop: allow the read surface (Chimera aocfo_* reads AND the
 * anaplan-ops show_/read_cells/run_export/get_ surface), deny every mutation and
 * anything unrecognised. The SDK requires an allow decision to echo the
 * (possibly unchanged) tool input as `updatedInput`.
 */
export function canUseTool(toolName: string, input: Record<string, unknown>): Decision {
  const allow: Decision = { behavior: "allow", updatedInput: input };
  if (ALLOWED_TOOLS.includes(toolName)) return allow;
  // explicit mutation/destructive denials (both surfaces)
  if (WRITE_RE.test(toolName) || DISALLOWED_TOOLS.includes(toolName) || OPS_DENY_RE.test(toolName)) {
    return { behavior: "deny", message: `${toolName} is a write/mutation — denied in the read-only web session (Phase 2, gated).` };
  }
  // ops read surface (public Azure endpoint)
  if (OPS_READ_RE.test(toolName)) return allow;
  // unknown Chimera reads (forward-compat) → allow; everything else → deny
  if (/^mcp__anaplan-chimera__aocfo_(catalog|sql|explain|get|set_model_context)/.test(toolName)) return allow;
  return { behavior: "deny", message: `${toolName} is not permitted in the read-only web session.` };
}
