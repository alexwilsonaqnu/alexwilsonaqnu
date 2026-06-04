import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const HOOKS = join(ROOT, ".claude", "hooks");

/** Run a hook script with a JSON payload on stdin. Returns {status, stdout, stderr}. */
export function runHook(script, payload, env = {}) {
  const path = join(HOOKS, script);
  const res = spawnSync(path, [], {
    input: JSON.stringify(payload),
    encoding: "utf8",
    env: { ...process.env, ...env },
  });
  return { status: res.status, stdout: res.stdout ?? "", stderr: res.stderr ?? "" };
}

/** Make an isolated temp ledger dir; optionally seed it with facts for a session. */
export function tempLedger(sessionId, facts = []) {
  const dir = mkdtempSync(join(tmpdir(), "fpna-ledger-"));
  if (facts.length) {
    mkdirSync(dir, { recursive: true });
    const lines = facts.map((f) => JSON.stringify(f)).join("\n") + "\n";
    writeFileSync(join(dir, `${sessionId}.jsonl`), lines, "utf8");
  }
  return dir;
}

export function readLedger(dir, sessionId) {
  const path = join(dir, `${sessionId}.jsonl`);
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf8")
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l));
}
