import { test } from "node:test";
import assert from "node:assert/strict";
import { runHook, tempLedger, readLedger } from "../helpers.mjs";

const SESSION = "sess-append";

const textResponse = (obj) => ({
  content: [{ type: "text", text: JSON.stringify(obj) }],
});

test("appends a fact per numeric cell from an SQL grid, with intersection", () => {
  const dir = tempLedger(SESSION, []);
  const payload = {
    session_id: SESSION,
    tool_name: "mcp__anaplan-chimera__aocfo_sql_query",
    tool_input: {
      module: "REP Revenue Variance",
      version: "Actual",
      query: 'SELECT a."revenue" - p."revenue" AS variance FROM "t" a JOIN "t" p ON 1=1',
    },
    tool_response: textResponse({
      columns: ["G2 Country", "Time", "variance"],
      rows: [
        ["Europe", "Q3 FY26", 250000],
        ["Americas", "Q3 FY26", -90000],
      ],
    }),
  };
  const r = runHook("ledger-append.py", payload, { FPNA_LEDGER_DIR: dir });
  assert.equal(r.status, 0, r.stderr);

  const facts = readLedger(dir, SESSION);
  assert.equal(facts.length, 2);
  const eu = facts.find((f) => f.intersection["G2 Country"] === "Europe");
  assert.ok(eu, "Europe fact present");
  assert.equal(eu.value, 250000);
  assert.equal(eu.lineItem, "variance");
  assert.equal(eu.source, "sql_calcite");
  assert.equal(eu.intersection.Time, "Q3 FY26");
  assert.ok(eu.query.includes("AS variance"));
  assert.match(eu.requestId, /^req_/);
});

test("explain_cell response still logs numeric leaves", () => {
  const dir = tempLedger(SESSION + "2", []);
  const payload = {
    session_id: SESSION + "2",
    tool_name: "mcp__anaplan-chimera__aocfo_explain_cell",
    tool_input: { module: "P&L" },
    tool_response: textResponse({ formula: "Volume * Price", components: { volume: 1000, price: 12.5 } }),
  };
  const r = runHook("ledger-append.py", payload, { FPNA_LEDGER_DIR: dir });
  assert.equal(r.status, 0, r.stderr);
  const facts = readLedger(dir, SESSION + "2");
  assert.ok(facts.length >= 2);
  assert.ok(facts.every((f) => f.source === "explain_cell"));
});

test("no session id → fails open (no ledger write, no error)", () => {
  const dir = tempLedger("unused", []);
  const r = runHook(
    "ledger-append.py",
    { tool_name: "mcp__anaplan-chimera__aocfo_sql_query", tool_response: textResponse({ rows: [] }) },
    { FPNA_LEDGER_DIR: dir, FPNA_SESSION_ID: "" },
  );
  assert.equal(r.status, 0, r.stderr);
});
