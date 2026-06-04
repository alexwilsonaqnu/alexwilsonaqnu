import { test } from "node:test";
import assert from "node:assert/strict";
import { runHook } from "../helpers.mjs";

const write = (tool, tool_input) => ({ tool_name: `mcp__anaplan-chimera__${tool}`, tool_input });

test("denies destructive run_delete outright", () => {
  const r = runHook("scenario-write-guard.sh", write("run_delete", { modelId: "dev", version: "Forecast" }));
  assert.equal(r.status, 2, r.stderr);
  assert.match(r.stderr, /never permitted/);
});

test("denies write to a production model", () => {
  const r = runHook(
    "scenario-write-guard.sh",
    write("aocfo_cell_write", { modelId: "ACME-production", version: "Forecast", approved: true }),
  );
  assert.equal(r.status, 2, r.stderr);
  assert.match(r.stderr, /PRODUCTION/);
});

test("denies write to the Actual version", () => {
  const r = runHook(
    "scenario-write-guard.sh",
    write("aocfo_cell_write", { modelId: "dev-sandbox", version: "Actual", approved: true }),
  );
  assert.equal(r.status, 2, r.stderr);
  assert.match(r.stderr, /Forecast\/Budget\/scenario/);
});

test("denies write with no explicit version", () => {
  const r = runHook("scenario-write-guard.sh", write("aocfo_cell_write", { modelId: "dev", approved: true }));
  assert.equal(r.status, 2, r.stderr);
});

test("denies unapproved write to a valid sandbox Forecast version", () => {
  const r = runHook(
    "scenario-write-guard.sh",
    write("aocfo_cell_write", { modelId: "dev-sandbox", version: "Forecast" }),
  );
  assert.equal(r.status, 2, r.stderr);
  assert.match(r.stderr, /not approved/);
});

test("allows approved write to a sandbox Forecast version", () => {
  const r = runHook(
    "scenario-write-guard.sh",
    write("aocfo_cell_write", { modelId: "dev-sandbox", version: "Forecast", approved: true }),
  );
  assert.equal(r.status, 0, r.stderr);
});

test("approval can come from the run context env", () => {
  const r = runHook(
    "scenario-write-guard.sh",
    write("aocfo_cell_write", { modelId: "dev-sandbox", version: "Scenario A" }),
    { FPNA_WRITE_APPROVED: "true" },
  );
  assert.equal(r.status, 0, r.stderr);
});
