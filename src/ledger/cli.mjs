#!/usr/bin/env node
// Inspect a session's provenance ledger: `npm run ledger:show <session-id>`
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const sessionId = process.argv[2];
if (!sessionId) {
  console.error("usage: npm run ledger:show <session-id>");
  process.exit(1);
}
const dir = process.env.FPNA_LEDGER_DIR ?? ".fpna/ledger";
const path = join(dir, `${sessionId}.jsonl`);
if (!existsSync(path)) {
  console.error(`no ledger at ${path}`);
  process.exit(1);
}

const facts = readFileSync(path, "utf8")
  .split("\n")
  .filter((l) => l.trim())
  .map((l) => JSON.parse(l));

console.log(`Ledger ${sessionId} — ${facts.length} fact(s)\n`);
for (const f of facts) {
  const slice = Object.entries(f.intersection ?? {})
    .map(([k, v]) => `${k}=${v}`)
    .join(", ");
  console.log(
    `  [${f.requestId}] ${f.value}  ·  ${f.label}  ·  ${f.source}` +
      (f.version ? `  ·  v=${f.version}` : "") +
      (slice ? `  ·  {${slice}}` : ""),
  );
}
