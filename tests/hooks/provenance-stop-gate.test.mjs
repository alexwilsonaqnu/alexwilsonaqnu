import { test } from "node:test";
import assert from "node:assert/strict";
import { runHook, tempLedger } from "../helpers.mjs";

const SESSION = "sess-test";

const fact = (value, label, extra = {}) => ({
  value,
  label,
  workspaceId: "ws",
  modelId: "mdl",
  module: "REP Revenue Variance",
  lineItem: label,
  intersection: { "G2 Country": "Europe", Time: "Q3 FY26" },
  source: "sql_calcite",
  requestId: `req_${label.replace(/\W/g, "").slice(0, 6)}`,
  fetchedAt: "2026-06-04T00:00:00Z",
  ...extra,
});

// ---- BLOCKS: the planted unsourced number (Sprint 0 "Done") ------------------
test("blocks a draft stating an unsourced number", () => {
  const dir = tempLedger(SESSION, [fact(1450000, "actual revenue")]);
  const r = runHook(
    "provenance-stop-gate.py",
    {
      session_id: SESSION,
      draft: "Revenue came in at $1,450,000, a variance of $250,000 to plan.",
    },
    { FPNA_LEDGER_DIR: dir },
  );
  // $250,000 is NOT in the ledger → must block
  assert.equal(r.status, 2, r.stderr);
  assert.match(r.stderr, /provenance-stop-gate.*BLOCKED/s);
  assert.match(r.stderr, /250,000/);
});

test("blocks an unsourced percentage", () => {
  const dir = tempLedger(SESSION, [fact(1450000, "actual revenue")]);
  const r = runHook(
    "provenance-stop-gate.py",
    { session_id: SESSION, draft: "Margin expanded 12.5% versus plan." },
    { FPNA_LEDGER_DIR: dir },
  );
  assert.equal(r.status, 2, r.stderr);
  assert.match(r.stderr, /12\.5%/);
});

// ---- ALLOWS: every stated number traces to a ledger fact ---------------------
test("allows a draft where every number is sourced", () => {
  const dir = tempLedger(SESSION, [
    fact(1450000, "actual revenue"),
    fact(250000, "revenue variance to plan"),
  ]);
  const r = runHook(
    "provenance-stop-gate.py",
    {
      session_id: SESSION,
      draft: "Revenue came in at $1,450,000, a variance of $250,000 to plan.",
    },
    { FPNA_LEDGER_DIR: dir },
  );
  assert.equal(r.status, 0, r.stderr);
});

test("matches percent stored as a fraction (12.5 ↔ 0.125)", () => {
  const dir = tempLedger(SESSION, [fact(0.125, "margin delta", { source: "sql_calcite" })]);
  const r = runHook(
    "provenance-stop-gate.py",
    { session_id: SESSION, draft: "Gross margin moved 12.5%." },
    { FPNA_LEDGER_DIR: dir },
  );
  assert.equal(r.status, 0, r.stderr);
});

test("ignores period ids, years and small counts", () => {
  const dir = tempLedger(SESSION, []);
  const r = runHook(
    "provenance-stop-gate.py",
    { session_id: SESSION, draft: "In Q3 FY26 (fiscal 2026) the top 3 drivers shifted." },
    { FPNA_LEDGER_DIR: dir },
  );
  assert.equal(r.status, 0, r.stderr);
});

test("allows fact-reference placeholders (no raw numerals)", () => {
  const dir = tempLedger(SESSION, []);
  const r = runHook(
    "provenance-stop-gate.py",
    { session_id: SESSION, draft: "Revenue was {{fact:req_abc12345}}, variance {{fact:req_def67890}}." },
    { FPNA_LEDGER_DIR: dir },
  );
  assert.equal(r.status, 0, r.stderr);
});

test("empty draft → pass", () => {
  const dir = tempLedger(SESSION, []);
  const r = runHook("provenance-stop-gate.py", { session_id: SESSION, draft: "" }, { FPNA_LEDGER_DIR: dir });
  assert.equal(r.status, 0, r.stderr);
});
