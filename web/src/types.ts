// Mirrors src/ledger/fact.ts — the provenance contract the UI renders.
export type FactSource = "line_item" | "sql_calcite" | "scenario_recompute" | "explain_cell";

export interface AnaplanFact {
  value: number | string;
  label: string;
  workspaceId: string;
  modelId: string;
  module: string;
  lineItem: string;
  intersection: Record<string, string>;
  version?: string;
  source: FactSource;
  query?: string;
  requestId: string;
  fetchedAt: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  /** requestIds of facts that backed this assistant turn. */
  factIds?: string[];
}

// Server-Sent Event payloads from POST /api/chat (see src/web/agent.ts).
export type AgentEvent =
  | { type: "start"; sessionId: string }
  | { type: "token"; text: string }
  | { type: "tool"; name: string; phase: "start" }
  | { type: "facts"; facts: AnaplanFact[] }
  | { type: "message"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; sessionId: string };
