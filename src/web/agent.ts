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
import { Ledger, factNumber, type AnaplanFact } from "../ledger/index.js";
import { openLedger } from "../orchestrator.js";
import { chimeraServerConfig, chimeraEnv } from "../mcp/chimera.js";
import { opsServerConfig, opsToken } from "../mcp/ops.js";
import { ALLOWED_TOOLS, DISALLOWED_TOOLS, canUseTool } from "./permissions.js";
import { collectFacts, verifyAnswer, isDataTool, type FactInput } from "./provenance.js";
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

/** Write a diagnostic snapshot of a tool result (exposed at GET /api/debug). */
function writeDebug(repoRoot: string, payload: unknown): void {
  try {
    mkdirSync(join(repoRoot, ".fpna", "debug"), { recursive: true });
    writeFileSync(join(repoRoot, ".fpna", "debug", "last-tool-response.json"), JSON.stringify(payload, null, 2));
  } catch {
    /* best-effort */
  }
}

export type AgentEvent =
  | { type: "token"; text: string }
  | { type: "tool"; name: string; phase: "start" }
  | { type: "mcp"; servers: { name: string; status: string }[] }
  | { type: "final"; text: string }
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
You are the FP&A Chief of Staff. You read Anaplan via anaplan-ops tools
(mcp__anaplan-ops__*) and answer in a crisp CFO register. Every call needs
workspaceId="${WS_GUID}" and a modelId — never ask the user for these.

BE DECISIVE AND EFFICIENT — you have a limited turn budget. Make the FEWEST calls
needed to read the answer. Do NOT re-explore, do NOT narrate every step, do NOT
second-guess. Once you find the module + the variance line item, go STRAIGHT to
read_cells.

MODEL: default to modelId "${MODEL_GUID}". Only call show_models (workspaceId=
"${WS_GUID}") if that model clearly cannot answer; never switch models more than once.

PROCEDURE (structure-first — this surface has NO SQL, so READ a stored line item;
never compute a delta):
  1. show_modules → pick ONE module (for variance, prefer a "Version Comparison" /
     "Variance" / REP / OUT report module).
  2. show_lineitems(includeAll=true) on that module → find the PRE-COMPUTED line item
     named Variance / Delta / vs Plan / B(W) / % Change.
  3. show_savedviews (and show_viewdetails only if you need the view's dimensions).
  4. read_cells(moduleId, viewId, pages for the time/version/entity slice) → READ the
     values. For a relative period ("last quarter/month"), resolve it from
     show_currentperiod / show_modelcalendar in ONE call, then read.
  5. Report the figures in a short markdown table. Stop.

THE INVARIANT (§0): never originate, sum, difference, multiply, divide, ratio, or
extrapolate a number. Read a stored variance/total line item; if none exists, report
the Actual and Plan you read (not a computed difference). Only numbers you actually
read may appear in your answer — they're captured to the provenance ledger.
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

/** Flatten an MCP tool_result's content into text. */
function resultText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((c) =>
        typeof c === "string"
          ? c
          : c && typeof c === "object" && "text" in (c as Record<string, unknown>)
            ? String((c as Record<string, unknown>).text ?? "")
            : "",
      )
      .join("\n");
  }
  if (content && typeof content === "object") return JSON.stringify(content);
  return content == null ? "" : String(content);
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
  const pendingTools = new Map<string, { name: string; input: Record<string, any> }>();
  const captured: FactInput[] = [];
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
        // NB: provenance (ledger population + answer vetting) is enforced IN-PROCESS
        // below — the Python/bash file hooks fail silently when a Finder-launched
        // app has no python3 on its PATH, so the web path can't depend on them.
        systemPrompt: { type: "preset", preset: "claude_code", append: surface.brief },
        model: MODEL,
        includePartialMessages: true,
        permissionMode: "default",
        allowedTools: ALLOWED_TOOLS,
        disallowedTools: DISALLOWED_TOOLS,
        canUseTool: async (toolName, input) => canUseTool(toolName, input as Record<string, unknown>),
        maxTurns: 60,
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
              pendingTools.set(block.id, { name: block.name, input: (block.input ?? {}) as Record<string, any> });
              yield { type: "tool", name: block.name, phase: "start" };
            }
          }
          break;
        }
        case "user": {
          // tool results come back as tool_result blocks on user messages —
          // capture figures from Anaplan reads into the ledger (in-process).
          const content = msg.message?.content ?? [];
          for (const block of Array.isArray(content) ? content : []) {
            if (block?.type === "tool_result") {
              const meta = pendingTools.get(block.tool_use_id) ?? { name: "", input: {} };
              const text = resultText(block.content);
              let added = 0;
              if (isDataTool(meta.name)) {
                const f = collectFacts(meta.name, meta.input, text);
                captured.push(...f);
                added = f.length;
              }
              writeDebug(repoRoot, { tool: meta.name, isDataTool: isDataTool(meta.name), factsCaptured: added, text: text.slice(0, 6000) });
            }
          }
          break;
        }
        case "result": {
          sawResult = true;
          if (typeof msg.result === "string" && msg.result.trim()) answer = msg.result;
          // surface real failures instead of finishing silently
          if (msg.is_error || (typeof msg.subtype === "string" && msg.subtype !== "success")) {
            const sub = String(msg.subtype ?? "");
            const friendly =
              sub === "error_max_turns"
                ? "The model ran out of steps before reading the numbers. Try a more specific question (one module / one period), e.g. \"Read Revenue Actual vs Plan from OUT IS Version Comparison for May 2026.\""
                : sub === "error_during_execution"
                  ? "The run hit an execution error partway through."
                  : typeof msg.result === "string" && msg.result
                    ? msg.result
                    : `Run ended: ${sub}`;
            yield { type: "error", message: friendly };
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

  // Replace the streamed reasoning with the clean final answer.
  if (answer.trim()) yield { type: "final", text: answer };

  // Persist the captured facts to the session ledger, then read them back.
  const ledger = new Ledger(sdkSessionId);
  for (const f of captured) ledger.append(f);
  const facts = ledger.all();
  yield { type: "facts", facts };

  // §0 backstop, in-process: every number in the finished answer must trace to a
  // ledger fact (a number we actually read from Anaplan). No python/bash needed.
  if (answer.trim()) {
    const { ok, unsourced } = verifyAnswer(answer, facts.map(factNumber));
    if (ok) {
      yield {
        type: "verify",
        ok: true,
        message: facts.length
          ? `Every figure traces to the ledger (${facts.length} fact${facts.length === 1 ? "" : "s"}).`
          : "No unsourced figures in this answer.",
      };
    } else {
      const shown = unsourced.slice(0, 6).map((n) => n.toLocaleString("en-US")).join(", ");
      yield {
        type: "verify",
        ok: false,
        message: `${unsourced.length} figure(s) not traceable to the ledger: ${shown}${unsourced.length > 6 ? " …" : ""}`,
      };
    }
  }
  yield { type: "done", sessionId };
}
