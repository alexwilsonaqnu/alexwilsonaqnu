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
import type { AnaplanFact } from "../ledger/index.js";
import { openLedger } from "../orchestrator.js";
import { ALLOWED_TOOLS, DISALLOWED_TOOLS, canUseTool } from "./permissions.js";

export type AgentEvent =
  | { type: "token"; text: string }
  | { type: "tool"; name: string; phase: "start" }
  | { type: "facts"; facts: AnaplanFact[] }
  | { type: "message"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; sessionId: string };

const MODEL = process.env.FPNA_MODEL ?? "claude-opus-4-8";

const ORCHESTRATOR_BRIEF = `
You are the FP&A Chief of Staff orchestrator. Route the user's finance question,
delegate retrieval to the anaplan-retriever subagent (the only agent that touches
Anaplan), and narrate the answer in a crisp CFO register.

THE INVARIANT: never originate, sum, difference, multiply, divide, ratio, annualize,
or extrapolate a number. Every figure must be read from an Anaplan line item or
computed by one Calcite query (engine-side). Walk the calculation hierarchy. If you
need a number you don't have, have the retriever fetch it — do not compute it.
`.trim();

/** Run one turn; yields normalized events for SSE. */
export async function* runTurn(
  message: string,
  sessionId: string,
  repoRoot: string,
): AsyncGenerator<AgentEvent> {
  const ledger = openLedger(sessionId); // sets FPNA_RUNTIME + FPNA_SESSION_ID
  const seen = new Set(ledger.all().map((f) => f.requestId));

  try {
    const response = query({
      prompt: message,
      options: {
        cwd: repoRoot,
        settingSources: ["project", "local"],
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
          if (typeof msg.result === "string" && msg.subtype !== "success") {
            yield { type: "message", text: msg.result };
          }
          break;
        }
        default:
          break;
      }
    }
  } catch (err) {
    yield { type: "error", message: err instanceof Error ? err.message : String(err) };
  }

  // surface every fact retrieved this turn (and the running session set)
  const facts = ledger.all();
  yield { type: "facts", facts };
  yield { type: "done", sessionId };

  void seen; // (reserved: per-turn diffing if we later stream incremental facts)
}
