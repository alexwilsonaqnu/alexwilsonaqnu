import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { runHook, tempLedger } from "../helpers.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const EVAL_DIR = join(ROOT, "evals", "provenance-evals");

/** Resolve {{fact:reqId}} placeholders to the ledger value — the assembler's job. */
function resolve(answer, ledger) {
  return answer.replace(/\{\{\s*fact:([^}]+)\}\}/g, (_, id) => {
    const f = ledger.find((x) => x.requestId === id.trim());
    if (!f) throw new Error(`unresolved placeholder: ${id}`);
    // simple presentation: thousands separators, like a real renderer would
    return typeof f.value === "number" ? f.value.toLocaleString("en-US") : String(f.value);
  });
}

const cases = readdirSync(EVAL_DIR).filter((f) => f.endsWith(".json"));

for (const file of cases) {
  const c = JSON.parse(readFileSync(join(EVAL_DIR, file), "utf8"));
  const session = `eval-${c.id}`;

  test(`provenance eval ${c.id}: resolved answer is 100% sourced`, () => {
    const dir = tempLedger(session, c.ledger);
    const resolved = resolve(c.answer, c.ledger);
    const r = runHook(
      "provenance-stop-gate.py",
      { session_id: session, draft: resolved },
      { FPNA_LEDGER_DIR: dir },
    );
    const expectSourced = c.expect.all_numbers_sourced === true;
    assert.equal(
      r.status,
      expectSourced ? 0 : 2,
      `gate exit ${r.status} for resolved="${resolved}"\n${r.stderr}`,
    );
  });

  test(`provenance eval ${c.id}: planted unsourced number is caught`, () => {
    // inject a number NOT in the ledger → the gate MUST block it
    const dir = tempLedger(session, c.ledger);
    const tampered = resolve(c.answer, c.ledger) + " (note: a stray $987,654 figure)";
    const r = runHook(
      "provenance-stop-gate.py",
      { session_id: session, draft: tampered },
      { FPNA_LEDGER_DIR: dir },
    );
    assert.equal(r.status, 2, r.stderr);
    assert.match(r.stderr, /987,654/);
  });
}
