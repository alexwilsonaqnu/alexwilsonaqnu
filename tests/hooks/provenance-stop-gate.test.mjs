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
    { FPNA_LEDGER_DIR: dir, FPNA_RUNTIME: "1" },
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
    { FPNA_LEDGER_DIR: dir, FPNA_RUNTIME: "1" },
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
    { FPNA_LEDGER_DIR: dir, FPNA_RUNTIME: "1" },
  );
  assert.equal(r.status, 0, r.stderr);
});

test("matches percent stored as a fraction (12.5 ↔ 0.125)", () => {
  const dir = tempLedger(SESSION, [fact(0.125, "margin delta", { source: "sql_calcite" })]);
  const r = runHook(
    "provenance-stop-gate.py",
    { session_id: SESSION, draft: "Gross margin moved 12.5%." },
    { FPNA_LEDGER_DIR: dir, FPNA_RUNTIME: "1" },
  );
  assert.equal(r.status, 0, r.stderr);
});

test("ignores period ids, years and small counts", () => {
  const dir = tempLedger(SESSION, []);
  const r = runHook(
    "provenance-stop-gate.py",
    { session_id: SESSION, draft: "In Q3 FY26 (fiscal 2026) the top 3 drivers shifted." },
    { FPNA_LEDGER_DIR: dir, FPNA_RUNTIME: "1" },
  );
  assert.equal(r.status, 0, r.stderr);
});

test("allows fact-reference placeholders (no raw numerals)", () => {
  const dir = tempLedger(SESSION, []);
  const r = runHook(
    "provenance-stop-gate.py",
    { session_id: SESSION, draft: "Revenue was {{fact:req_abc12345}}, variance {{fact:req_def67890}}." },
    { FPNA_LEDGER_DIR: dir, FPNA_RUNTIME: "1" },
  );
  assert.equal(r.status, 0, r.stderr);
});

test("empty draft → pass", () => {
  const dir = tempLedger(SESSION, []);
  const r = runHook("provenance-stop-gate.py", { session_id: SESSION, draft: "" }, { FPNA_LEDGER_DIR: dir, FPNA_RUNTIME: "1" });
  assert.equal(r.status, 0, r.stderr);
});

// ---- runtime scoping: enforce only inside the FP&A runtime -------------------
test("outside the FP&A runtime, unsourced numbers pass (no deliverable to vet)", () => {
  const dir = tempLedger(SESSION, []);
  // no FPNA_RUNTIME, no FPNA_SESSION_ID → a plain dev/interactive session
  const r = runHook(
    "provenance-stop-gate.py",
    { session_id: SESSION, draft: "33 tests pass and the ops surface is in §2B." },
    { FPNA_LEDGER_DIR: dir, FPNA_SESSION_ID: "" },
  );
  assert.equal(r.status, 0, r.stderr);
});

test("the dangerous case still blocks: FP&A runtime, empty ledger, stated numbers", () => {
  const dir = tempLedger(SESSION, []); // retrieval silently produced nothing
  const r = runHook(
    "provenance-stop-gate.py",
    { session_id: SESSION, draft: "Revenue was $1,450,000 this quarter." },
    { FPNA_LEDGER_DIR: dir, FPNA_RUNTIME: "1" },
  );
  assert.equal(r.status, 2, r.stderr);
  assert.match(r.stderr, /1,450,000/);
});

test("FPNA_SESSION_ID alone activates enforcement", () => {
  const dir = tempLedger(SESSION, []);
  const r = runHook(
    "provenance-stop-gate.py",
    { session_id: SESSION, draft: "Variance was $250,000." },
    { FPNA_LEDGER_DIR: dir, FPNA_SESSION_ID: SESSION },
  );
  assert.equal(r.status, 2, r.stderr);
});
