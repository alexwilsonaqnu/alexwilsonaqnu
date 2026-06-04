#!/usr/bin/env bash
# PreToolUse → no-math-gate (§7). Matcher: Bash.
#
# Blocks (exit 2) any command that performs FINANCIAL ARITHMETIC on values
# destined for output — the Tier-3 violation of the calculation hierarchy (§4).
# Arithmetic is delegation, not computation: a variance/%/YoY must come from a
# structural Anaplan line item or one Calcite query, never from the LLM, Python,
# or a shell.
#
# This gate is intentionally strict and will occasionally false-positive. That
# is the correct trade: a blocked legitimate format call costs one retry; a
# permitted LLM-computed variance corrupts a board deck. TUNE BY WIDENING THE
# ALLOW LIST (formatting patterns), NEVER BY LOOSENING THE DENY ON ARITHMETIC.
set -euo pipefail

INPUT="$(cat)"

# Extract the command string from the hook payload (stdlib python, no deps).
CMD="$(
python3 - "$INPUT" <<'PY'
import json, sys
try:
    p = json.loads(sys.argv[1])
except Exception:
    print(""); raise SystemExit
print((p.get("tool_input") or {}).get("command", ""))
PY
)"

[ -z "$CMD" ] && exit 0

deny() {
  cat >&2 <<EOF
[no-math-gate] BLOCKED: this command performs arithmetic on values destined for
output, which violates the no-math invariant (§0/§4).

  offending pattern: $1

The model never makes a number. Instead:
  • read a structural line item that already holds the answer
    (look for *Variance / Delta / YoY / % Change / Growth* via aocfo_catalog_line_items), OR
  • request the derived value as ONE Calcite query from anaplan-retriever
    (e.g. SELECT a."revenue" - b."revenue" AS rev_delta ...), so Anaplan computes it.

Formatting/rounding a SINGLE already-fetched ledger value is allowed.
EOF
  exit 2
}

# ----------------------------------------------------------------------------
# 1) pandas / numpy aggregation & change operators on output-bound series
# ----------------------------------------------------------------------------
if grep -Eiq '\.(pct_change|diff|cumsum|cumprod|rolling)\s*\(' <<<"$CMD"; then
  deny "pandas change op (.pct_change/.diff/.cumsum/...)"
fi
if grep -Eiq '\.(sum|mean|median|std|var|prod)\s*\(\s*\)' <<<"$CMD"; then
  deny "pandas/numpy aggregation (.sum()/.mean()/.std()/...)"
fi
if grep -Eiq '\bnp\.(sum|mean|subtract|divide|prod|diff|cumsum|average)\b' <<<"$CMD"; then
  deny "numpy arithmetic (np.subtract/np.divide/np.mean/...)"
fi

# ----------------------------------------------------------------------------
# 2) named finance arithmetic: variance / growth / margin computed in-prose
#    e.g.  variance = actual - budget ;  growth = (cur - prev) / prev
# ----------------------------------------------------------------------------
if grep -Eiq '(variance|delta|yoy|growth|margin|run.?rate)\s*=\s*[^=]*[-+*/]' <<<"$CMD"; then
  deny "manual variance/growth/margin computation"
fi
if grep -Eiq '(actual|budget|forecast|plan|prior|current)[a-z_]*\s*[-+*/]\s*(actual|budget|forecast|plan|prior|current|[a-z_0-9]+)' <<<"$CMD"; then
  deny "arithmetic across finance series (e.g. actual - budget)"
fi

# ----------------------------------------------------------------------------
# 3) raw number-operator-number — ONLY in an arithmetic-evaluation context.
#    Plain file/text tooling (grep, find, unzip, ...) is never "math destined
#    for output": a UUID path or a regex char class like [0-9] is not a
#    subtraction. So this rule fires only when the command actually evaluates
#    arithmetic (an interpreter, bc/expr/let, or shell $(( ))). The finance
#    rules above stay global. This widens the ALLOW list; it does not loosen
#    the DENY on real computation (python literal math is still caught).
# ----------------------------------------------------------------------------
if grep -Eiq '(^|[^[:alnum:]_])(python3?|node|deno|bun|ruby|perl|php|bc|expr|let)\b|\$\(\(|awk[^|]*print' <<<"$CMD"; then
  # mask non-arithmetic digit contexts before the literal check
  MASKED="$(
python3 - "$CMD" <<'PY'
import re, sys
c = sys.argv[1]
c = re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b', ' ', c)  # UUIDs
c = re.sub(r'\[[^\]]*\]', ' ', c)                      # bracketed: regex char classes [0-9], indices a[1-2]
c = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', ' ', c)           # ISO dates
c = re.sub(r'(?i)\b(?:FY|Q|H|CY|P)\s?\d{1,4}\b', ' ', c)  # period ids
c = re.sub(r'\b\d+\.\d+\.\d+\b', ' ', c)               # semver
c = re.sub(r'(^|\s)-\d+\b', ' ', c)                    # negative flags / args
print(c)
PY
)"
  if grep -Eq '[0-9][0-9,]*(\.[0-9]+)?[[:space:]]*[-+*/][[:space:]]*[0-9]' <<<"$MASKED"; then
    deny "literal arithmetic (number operator number)"
  fi
fi

exit 0
