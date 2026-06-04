/**
 * Bridge between the web server and the Claude Agent SDK.
 *
 * One chat turn = one `query()` run of the FP&A orchestrator, with the project's
 * `.claude/` harness loaded (settingSources: project → MCP servers + file hooks +
 * subagents + skills; local → Anaplan creds). The orchestrator delegates to the
 * `anaplan-retriever` subagent, whose reads land in the provenance ledger via the
 * ledger-append hook. We snapshot the ledger and surface the facts so the UI can
 * show *exactly* which Anaplan numbers backed the answer.
 *
 * The model never makes a number here — it is the same harness as the CLI, so the
 * no-math gate and provenance Stop-gate apply unchanged.
 */
import { query } from "@anthropic-ai/claude-agent-sdk";
import { Ledger, type AnaplanFact } from "../ledger/index.js";
import { openLedger } from "../orchestrator.js";
import { chimeraServerConfig } from "../mcp/chimera.js";
import { ALLOWED_TOOLS, DISALLOWED_TOOLS, canUseTool } from "./permissions.js";
import { buildHooks } from "./hooks-bridge.js";

export type AgentEvent =
  | { type: "token"; text: string }
  | { type: "tool"; name: string; phase: "start" }
  | { type: "facts"; facts: AnaplanFact[] }
  | { type: "message"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; sessionId: string };

// Default to a model proven available on this API key. Override with FPNA_MODEL
// (e.g. an Opus tier) if your account has access.
const MODEL = process.env.FPNA_MODEL ?? "claude-sonnet-4-6";

const ORCHESTRATOR_BRIEF = `
You are the FP&A Chief of Staff. You BOTH retrieve and narrate. Answer the user's
finance question in a crisp CFO register.

RETRIEVE DIRECTLY — do not delegate to a subagent. Call the Anaplan tools yourself
(mcp__anaplan-chimera__aocfo_*). Follow the calculation hierarchy from the
anaplan-module-querier skill: catalog modules/line items → aocfo_sql_schema (with
included_objects) → aocfo_sql_query. The SQL contract: included_objects on every
schema and query call; slice-XOR-leaf per dimension; safe aliases (fy26/fy25, never
cur/prev); no CTEs. Use aocfo_explain_cell to drill 'why'.

THE INVARIANT (§0): never originate, sum, difference, multiply, divide, ratio,
annualize, or extrapolate a number. Every figure must be READ from a line item or
returned by ONE Calcite query (engine computes it, e.g. ... AS revenue_delta). If
you don't have a number, fetch it — never assert or estimate one. Numbers you read
land in the provenance ledger automatically; only ledger-backed figures may appear
in your answer.
`.trim();

/** Run one turn; yields normalized events for SSE. */
export async function* runTurn(
  message: string,
  sessionId: string,
  repoRoot: string,
): AsyncGenerator<AgentEvent> {
  openLedger(sessionId); // marks the FP&A runtime so the Stop-gate enforces

  // The ledger-append hook keys facts by the SDK's own session id (the value it
  // receives in the hook payload), not our sessionId — so capture it from the
  // message stream and read the ledger from THAT file. Otherwise the panel is
  // empty even when numbers were fetched.
  let sdkSessionId = sessionId;
  let sawResult = false;

  try {
    const response = query({
      prompt: message,
      options: {
        cwd: repoRoot,
        settingSources: ["project", "local"],
        // Pass the MCP server explicitly: the SDK does not reliably auto-load
        // mcpServers from settings.json, and takes args verbatim (creds resolved in code).
        mcpServers: { "anaplan-chimera": chimeraServerConfig() },
        // Wire the file hooks explicitly — the SDK does not load settings.json
        // hooks, and without these the web path would run ungoverned (§0).
        hooks: buildHooks(repoRoot),
        systemPrompt: { type: "preset", preset: "claude_code", append: ORCHESTRATOR_BRIEF },
        model: MODEL,
        includePartialMessages: true,
        permissionMode: "default",
        allowedTools: ALLOWED_TOOLS,
        disallowedTools: DISALLOWED_TOOLS,
        canUseTool: async (toolName, input) => canUseTool(toolName, input as Record<string, unknown>),
        maxTurns: 24,
      },
    });

    for await (const msg of response as AsyncIterable<Record<string, any>>) {
      if (typeof msg.session_id === "string") sdkSessionId = msg.session_id;
      switch (msg.type) {
        case "partial":
        case "stream_event": {
          const delta = msg.delta ?? msg.event?.delta;
          if (delta?.type === "text_delta" && typeof delta.text === "string") {
            yield { type: "token", text: delta.text };
          }
          break;
        }
        case "assistant": {
          const content = msg.message?.content ?? [];
          for (const block of content) {
            if (block?.type === "tool_use" && typeof block.name === "string") {
              yield { type: "tool", name: block.name, phase: "start" };
            }
          }
          break;
        }
        case "result": {
          sawResult = true;
          // surface real failures instead of finishing silently
          if (msg.is_error || (typeof msg.subtype === "string" && msg.subtype !== "success")) {
            const detail = typeof msg.result === "string" && msg.result ? msg.result : msg.subtype;
            yield { type: "error", message: `Run ended: ${detail}` };
          }
          break;
        }
        default:
          break;
      }
    }
    if (!sawResult) {
      yield { type: "error", message: `No response from the model (check that '${MODEL}' is available on this API key; override with FPNA_MODEL).` };
    }
  } catch (err) {
    yield { type: "error", message: err instanceof Error ? err.message : String(err) };
  }

  // read facts from the SDK-session ledger (where the hook actually wrote them)
  const facts = new Ledger(sdkSessionId).all();
  yield { type: "facts", facts };
  yield { type: "done", sessionId };
}
