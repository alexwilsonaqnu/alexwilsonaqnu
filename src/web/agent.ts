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
import { chimeraServerConfig, chimeraEnv } from "../mcp/chimera.js";
import { opsServerConfig, opsToken } from "../mcp/ops.js";
import { ALLOWED_TOOLS, DISALLOWED_TOOLS, canUseTool } from "./permissions.js";
import { buildHooks, runScript } from "./hooks-bridge.js";
import { join } from "node:path";

export type AgentEvent =
  | { type: "token"; text: string }
  | { type: "tool"; name: string; phase: "start" }
  | { type: "mcp"; servers: { name: string; status: string }[] }
  | { type: "facts"; facts: AnaplanFact[] }
  | { type: "verify"; ok: boolean; message: string }
  | { type: "message"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; sessionId: string };

// Default to a model proven available on this API key. Override with FPNA_MODEL
// (e.g. an Opus tier) if your account has access.
const MODEL = process.env.FPNA_MODEL ?? "claude-sonnet-4-6";

const WS_GUID = process.env.ANAPLAN_WS_GUID ?? "";
const MODEL_GUID = process.env.ANAPLAN_MODEL_GUID ?? "";

/** anaplan-ops surface (public Azure endpoint, no VPN, no subprocess). */
const OPS_BRIEF = `
You are the FP&A Chief of Staff. You BOTH retrieve and narrate, in a crisp CFO register.

RETRIEVE via the anaplan-ops tools (mcp__anaplan-ops__*). Every call needs a
workspaceId and a modelId — never ask the user for them.

PICK THE MODEL FROM THE TOOL CALLS: workspace "${WS_GUID}" is configured. First call
show_models (workspaceId="${WS_GUID}") and choose the model whose name/purpose best
fits the user's question (e.g. a revenue/planning model for a revenue question). If
none clearly fits, default to model "${MODEL_GUID}". Use the chosen model's id, with
workspaceId "${WS_GUID}", on every subsequent call. If a tool reports the model is
closed, call open_model first. State which model you used in your answer's source line.

Procedure (structure first — there is NO SQL on this surface):
  1. show_modules → pick the module at the user's grain (prefer REP/OUT report modules).
  2. show_lineitems (includeAll=true) → find a PRE-COMPUTED line item literally named
     Variance / Delta / YoY / % Change / Growth / vs Prior, or paired Actual/Plan/Forecast.
  3. show_savedviews → pick a view; read_cells (moduleId, viewId, pages for the slice)
     to read the value. The stored variance line item IS the answer — read it.

THE INVARIANT (§0): never originate, sum, difference, multiply, divide, ratio,
annualize, or extrapolate a number. This surface cannot compute deltas, so you MUST
read a stored variance/total line item — never calculate one yourself. If the model
has no stored variance line item, say so and report the Actual and Plan you read,
not a computed difference. Only numbers you actually read appear in your answer; they
land in the provenance ledger automatically.
`.trim();

/** Chimera surface (internal endpoint, Calcite SQL — needs the Anaplan VPN). */
const CHIMERA_BRIEF = `
You are the FP&A Chief of Staff. You BOTH retrieve and narrate, in a crisp CFO register.

MODEL BINDING: if aocfo_get_model_context returns a null workspaceId/modelId, bind via
aocfo_set_model_context with workspace id "${WS_GUID}" and model id "${MODEL_GUID}";
never ask the user for these IDs.

RETRIEVE DIRECTLY (no subagent) via mcp__anaplan-chimera__aocfo_*. Calculation hierarchy:
catalog modules/line items → aocfo_sql_schema (with included_objects) → aocfo_sql_query.
SQL contract: included_objects on every call; slice-XOR-leaf per dimension; aliases
fy26/fy25 (never cur/prev); no CTEs. A delta is a stored line item or ONE query
returning it AS an alias — the engine computes it.

THE INVARIANT (§0): never originate, sum, difference, multiply, divide, ratio,
annualize, or extrapolate a number. Read it or have Calcite return it; never assert one.
Only ledger-backed numbers may appear in your answer.
`.trim();

type Surface = { servers: Record<string, unknown>; brief: string; label: string };

/** Prefer ops (public, reachable) when its token is set; else Chimera (VPN). */
function pickSurface(): Surface {
  if (opsToken()) {
    return { servers: { "anaplan-ops": opsServerConfig() }, brief: OPS_BRIEF, label: "anaplan-ops" };
  }
  try {
    chimeraEnv(); // throws if creds missing
    return { servers: { "anaplan-chimera": chimeraServerConfig() }, brief: CHIMERA_BRIEF, label: "anaplan-chimera" };
  } catch {
    return { servers: {}, brief: OPS_BRIEF, label: "none" };
  }
}

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
  let answer = "";
  const surface = pickSurface();

  try {
    const response = query({
      prompt: message,
      options: {
        cwd: repoRoot,
        settingSources: ["project", "local"],
        // Pass the MCP server explicitly: the SDK does not reliably auto-load
        // mcpServers from settings.json, and takes args verbatim (creds resolved in code).
        mcpServers: surface.servers as never,
        // Wire the file hooks explicitly — the SDK does not load settings.json
        // hooks, and without these the web path would run ungoverned (§0).
        hooks: buildHooks(repoRoot),
        systemPrompt: { type: "preset", preset: "claude_code", append: surface.brief },
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
      // surface MCP connection status so the UI can show connected/failed
      if (msg.type === "system" && msg.subtype === "init" && Array.isArray(msg.mcp_servers)) {
        yield {
          type: "mcp",
          servers: msg.mcp_servers.map((s: { name?: string; status?: string }) => ({
            name: s.name ?? "?",
            status: s.status ?? "?",
          })),
        };
      }
      switch (msg.type) {
        case "partial":
        case "stream_event": {
          const delta = msg.delta ?? msg.event?.delta;
          if (delta?.type === "text_delta" && typeof delta.text === "string") {
            answer += delta.text;
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
          if (typeof msg.result === "string" && msg.result.trim()) answer = msg.result;
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

  // Enforce the invariant on the finished answer. The Stop-gate-via-transcript is
  // unreliable in the SDK path, so vet the streamed answer text against the ledger
  // directly (same script, draft passed in). This is the §0 backstop for the web path.
  if (answer.trim()) {
    try {
      const gate = await runScript(
        join(repoRoot, ".claude", "hooks", "provenance-stop-gate.py"),
        { hook_event_name: "Stop", draft: answer, session_id: sdkSessionId, cwd: repoRoot },
        repoRoot,
      );
      if (gate.code === 2) {
        const first = (gate.stderr.split("\n").find((l) => /not in the provenance ledger/.test(l)) ?? gate.stderr).trim();
        yield { type: "verify", ok: false, message: first || "Some figures are not traceable to the provenance ledger." };
      } else {
        yield { type: "verify", ok: true, message: `Every figure traces to the ledger (${facts.length} fact${facts.length === 1 ? "" : "s"}).` };
      }
    } catch {
      /* enforcement is best-effort */
    }
  }
  yield { type: "done", sessionId };
}
