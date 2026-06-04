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

const has = (k: string) => Boolean(process.env[k]) && !process.env[k]?.startsWith("<");

/**
 * Readiness for the web path (never returns values). Needs an API key, the
 * workspace/model IDs, and EITHER the ops Bearer token (public, no VPN — the
 * preferred surface) OR the Chimera Basic auth (internal, needs VPN).
 */
export function credsStatus(): { ready: boolean; missing: string[]; surface: string } {
  const missing: string[] = [];
  if (!has("ANTHROPIC_API_KEY") && !has("CLAUDE_CODE_OAUTH_TOKEN")) missing.push("ANTHROPIC_API_KEY");
  if (!has("ANAPLAN_WS_GUID")) missing.push("ANAPLAN_WS_GUID");
  if (!has("ANAPLAN_MODEL_GUID")) missing.push("ANAPLAN_MODEL_GUID");

  const ops = has("ANAPLAN_OPS_TOKEN");
  const chimera = has("ANAPLAN_BASIC_AUTH");
  const surface = ops ? "anaplan-ops" : chimera ? "anaplan-chimera" : "none";
  if (!ops && !chimera) missing.push("ANAPLAN_OPS_TOKEN (or ANAPLAN_BASIC_AUTH)");

  return { ready: missing.length === 0, missing, surface };
}
