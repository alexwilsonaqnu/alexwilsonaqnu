/**
 * Wire the project's file hooks into the Agent SDK explicitly.
 *
 * The SDK does not auto-load `.claude/settings.json` hooks (same as mcpServers),
 * so without this the web path would run UNGOVERNED — no ledger, no no-math gate,
 * no provenance Stop-gate. That defeats the §0 invariant. Here we register SDK
 * hook callbacks that shell out to the very same scripts the CLI uses, so the web
 * app and the CLI enforce identically.
 *
 * The HookInput the SDK passes (snake_case: tool_name, tool_input, tool_response,
 * session_id, cwd, transcript_path) is exactly the payload the Python/bash hooks
 * already parse from stdin, so the scripts run unchanged.
 */
import { spawn } from "node:child_process";
import { join } from "node:path";
import type { HookInput, HookJSONOutput } from "@anthropic-ai/claude-agent-sdk";

interface ScriptResult {
  code: number;
  stderr: string;
}

export function runScript(scriptPath: string, input: unknown, cwd: string): Promise<ScriptResult> {
  return new Promise((resolve) => {
    const child = spawn(scriptPath, [], { cwd, stdio: ["pipe", "pipe", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (d) => (stderr += d.toString()));
    child.on("error", (e) => resolve({ code: 0, stderr: `hook spawn failed: ${e.message}` }));
    child.on("close", (code) => resolve({ code: code ?? 0, stderr }));
    child.stdin.write(JSON.stringify(input));
    child.stdin.end();
  });
}

export function buildHooks(repoRoot: string): NonNullable<
  NonNullable<Parameters<typeof import("@anthropic-ai/claude-agent-sdk")["query"]>[0]["options"]>["hooks"]
> {
  const HOOKS = join(repoRoot, ".claude", "hooks");
  const noMath = join(HOOKS, "no-math-gate.sh");
  const ledgerAppend = join(HOOKS, "ledger-append.py");
  const stopGate = join(HOOKS, "provenance-stop-gate.py");
  const backup = join(HOOKS, "backup-ledger.sh");

  return {
    // PreToolUse → no-math gate (the script self-no-ops on non-Bash tools).
    PreToolUse: [
      {
        hooks: [
          async (input: HookInput): Promise<HookJSONOutput> => {
            const { code, stderr } = await runScript(noMath, input, repoRoot);
            if (code === 2) {
              return {
                hookSpecificOutput: {
                  hookEventName: "PreToolUse",
                  permissionDecision: "deny",
                  permissionDecisionReason: stderr.trim() || "Blocked by no-math-gate.",
                },
              };
            }
            return { continue: true };
          },
        ],
      },
    ],

    // PostToolUse → ledger-append, but ONLY for Anaplan tools (so non-Anaplan
    // numbers — a Read of a file, a Bash echo — never pollute the allowlist).
    PostToolUse: [
      {
        hooks: [
          async (input: HookInput): Promise<HookJSONOutput> => {
            const name = (input as { tool_name?: string }).tool_name ?? "";
            if (!name.startsWith("mcp__anaplan")) return { continue: true };
            await runScript(ledgerAppend, input, repoRoot);
            return { continue: true };
          },
        ],
      },
    ],

    // Stop → provenance Stop-gate (the backstop: no unsourced number leaves).
    Stop: [
      {
        hooks: [
          async (input: HookInput): Promise<HookJSONOutput> => {
            const { code, stderr } = await runScript(stopGate, input, repoRoot);
            if (code === 2) {
              return { decision: "block", reason: stderr.trim() || "Unsourced number blocked by provenance Stop-gate." };
            }
            return { continue: true };
          },
        ],
      },
    ],

    // PreCompact → back up the ledger before context loss.
    PreCompact: [
      {
        hooks: [
          async (input: HookInput): Promise<HookJSONOutput> => {
            await runScript(backup, input, repoRoot);
            return { continue: true };
          },
        ],
      },
    ],
  };
}
