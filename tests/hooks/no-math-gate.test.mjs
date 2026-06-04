import { test } from "node:test";
import assert from "node:assert/strict";
import { runHook } from "../helpers.mjs";

const bash = (command) => ({
  hook_event_name: "PreToolUse",
  tool_name: "Bash",
  tool_input: { command },
});

// ---- BLOCKS: the planted LLM-subtraction and friends (Sprint 0 "Done") -------
test("blocks the planted LLM variance subtraction", () => {
  const r = runHook("no-math-gate.sh", bash('python3 -c "print(1450000 - 1200000)"'));
  assert.equal(r.status, 2, r.stderr);
  assert.match(r.stderr, /no-math-gate.*BLOCKED/s);
});

test("blocks named variance arithmetic", () => {
  const r = runHook("no-math-gate.sh", bash("variance=$((actual - budget))"));
  assert.equal(r.status, 2, r.stderr);
});

test("blocks finance-series arithmetic (actual - budget)", () => {
  const r = runHook("no-math-gate.sh", bash('echo $(( actual_revenue - budget_revenue ))'));
  assert.equal(r.status, 2, r.stderr);
});

test("blocks pandas pct_change", () => {
  const r = runHook("no-math-gate.sh", bash('python3 -c "df[\'rev\'].pct_change()"'));
  assert.equal(r.status, 2, r.stderr);
});

test("blocks pandas .sum() aggregation", () => {
  const r = runHook("no-math-gate.sh", bash('python3 -c "series.sum()"'));
  assert.equal(r.status, 2, r.stderr);
});

test("blocks numpy subtract", () => {
  const r = runHook("no-math-gate.sh", bash('python3 -c "np.subtract(a, b)"'));
  assert.equal(r.status, 2, r.stderr);
});

test("blocks growth ratio computed in-prose", () => {
  const r = runHook("no-math-gate.sh", bash("growth = (cur - prev) / prev"));
  assert.equal(r.status, 2, r.stderr);
});

// ---- ALLOWS: formatting / file I/O / tooling (false-positive budget) ---------
test("allows rounding a single fetched value", () => {
  const r = runHook("no-math-gate.sh", bash('python3 -c "print(round(value, 1))"'));
  assert.equal(r.status, 0, r.stderr);
});

test("allows string formatting", () => {
  const r = runHook("no-math-gate.sh", bash('printf "Revenue: %s\\n" "$rev"'));
  assert.equal(r.status, 0, r.stderr);
});

test("allows ordinary file/dir ops", () => {
  const r = runHook("no-math-gate.sh", bash("mkdir -p outputs && ls -la outputs"));
  assert.equal(r.status, 0, r.stderr);
});

test("allows ISO dates (not arithmetic)", () => {
  const r = runHook("no-math-gate.sh", bash('echo "as of 2026-06-04"'));
  assert.equal(r.status, 0, r.stderr);
});

test("allows period ids and semver", () => {
  const r = runHook("no-math-gate.sh", bash('echo "FY26 Q3, tsc 5.5.0"'));
  assert.equal(r.status, 0, r.stderr);
});

test("allows npm test / tsc tooling", () => {
  const r = runHook("no-math-gate.sh", bash("npm run typecheck && node --test tests/"));
  assert.equal(r.status, 0, r.stderr);
});

test("no command → pass", () => {
  const r = runHook("no-math-gate.sh", { tool_name: "Bash", tool_input: {} });
  assert.equal(r.status, 0, r.stderr);
});

// ---- ALLOWS: dev tooling whose digits aren't arithmetic (allow-list widening) ----
test("allows grep with a regex char class [0-9] (not 0 minus 9)", () => {
  const r = runHook("no-math-gate.sh", bash("grep -oE -- '--[a-zA-Z0-9_-]+' assets/*.css"));
  assert.equal(r.status, 0, r.stderr);
});

test("allows unzip of a path containing a UUID (e.g. 0732cf30-4a17-4830-bfdb-a83d6acf8288)", () => {
  const r = runHook(
    "no-math-gate.sh",
    bash("unzip -o -q /root/.claude/uploads/0732cf30-4a17-4830-bfdb-a83d6acf8288/x.zip -d /tmp/ds"),
  );
  assert.equal(r.status, 0, r.stderr);
});

test("allows find/sort/head pipeline", () => {
  const r = runHook("no-math-gate.sh", bash("find . -type f | sort | head -50"));
  assert.equal(r.status, 0, r.stderr);
});

test("allows 'anaplan-chimera' literal (plan inside anaplan is not plan - chimera)", () => {
  const r = runHook("no-math-gate.sh", bash('curl -sw "anaplan-chimera: %{http_code}" https://x.anaplan.com/mcp'));
  assert.equal(r.status, 0, r.stderr);
});

test("still blocks real finance-series arithmetic at a word boundary", () => {
  const r = runHook("no-math-gate.sh", bash("echo $(( plan - budget ))"));
  assert.equal(r.status, 2, r.stderr);
});

// ---- still BLOCKS real interpreter math (deny is intact) ---------------------
test("still blocks python literal subtraction even with the context gate", () => {
  const r = runHook("no-math-gate.sh", bash('python3 -c "print(1450000 - 1200000)"'));
  assert.equal(r.status, 2, r.stderr);
});

test("still blocks shell annualization $((monthly * 12))", () => {
  const r = runHook("no-math-gate.sh", bash("echo $(( 1000000 * 12 ))"));
  assert.equal(r.status, 2, r.stderr);
});
