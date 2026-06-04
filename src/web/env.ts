/**
 * Load secrets/GUInto process.env before the Agent SDK launches the MCP
 * subprocess, so `${ANAPLAN_BASIC_AUTH}` / `${ANAPLAN_WS_GUID}` /
 * `${ANAPLAN_MODEL_GUID}` (and the ops Bearer token, Phase 2) resolve.
 *
 * Source of truth is `.claude/settings.local.json` (gitignored). Anything
 * already in the real process env wins, so you can also export them directly.
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

export interface AnaplanCreds {
  ANAPLAN_BASIC_AUTH?: string;
  ANAPLAN_WS_GUID?: string;
  ANAPLAN_MODEL_GUID?: string;
  ANAPLAN_OPS_TOKEN?: string;
}

export function loadLocalEnv(repoRoot: string): void {
  const path = join(repoRoot, ".claude", "settings.local.json");
  if (!existsSync(path)) return;
  try {
    const raw = JSON.parse(readFileSync(path, "utf8")) as { env?: Record<string, string> };
    const env = raw.env ?? {};
    for (const [k, v] of Object.entries(env)) {
      if (process.env[k] === undefined && typeof v === "string" && !v.startsWith("<")) {
        process.env[k] = v;
      }
    }
  } catch {
    /* malformed local settings — ignore, creds check below will surface it */
  }
}

/** Which Anaplan Chimera credentials are present (never returns the values). */
export function credsStatus(): { ready: boolean; missing: string[] } {
  const required = ["ANAPLAN_BASIC_AUTH", "ANAPLAN_WS_GUID", "ANAPLAN_MODEL_GUID"];
  const missing: string[] = required.filter((k) => !process.env[k] || process.env[k]?.startsWith("<"));
  const anthropic = process.env.ANTHROPIC_API_KEY || process.env.CLAUDE_CODE_OAUTH_TOKEN;
  if (!anthropic) missing.push("ANTHROPIC_API_KEY");
  return { ready: missing.length === 0, missing };
}
