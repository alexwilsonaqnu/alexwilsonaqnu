#!/usr/bin/env bash
# PreToolUse → scenario-write-guard (§7).
# Matcher: mcp__anaplan-.*(write|import|process|delete|close|set_|reset).*
#
# Three jobs, in order:
#   1) DENY destructive ops outright (they belong to humans, never agents).
#   2) DENY any write whose target resolves to a PRODUCTION model or the
#      Actual/Current version.
#   3) Otherwise REQUIRE approval (approved:true in the run context) and force
#      writes onto a Forecast/Budget/named-scenario version on a Dev/sandbox model.
#
# Included in the skeleton from Sprint 0 even though writes ship in Phase 2, so
# the guard exists before the first write tool is ever wired.
set -euo pipefail

INPUT="$(cat)"

# Newline-delimited so model/version values containing spaces survive parsing.
PARSED="$(
python3 - "$INPUT" <<'PY'
import json, os, sys
try:
    p = json.loads(sys.argv[1])
except Exception:
    print("-\n-\n-\nfalse"); raise SystemExit
tool = (p.get("tool_name") or "").split("__")[-1]
ti = p.get("tool_input") or {}
model = (ti.get("modelId") or ti.get("model") or os.environ.get("ANAPLAN_MODEL_GUID","") or "").lower()
ver   = (ti.get("version") or ti.get("versionName") or "").lower()
# approval may come from the tool_input, the run context env, or a signed cron config
approved = str(ti.get("approved") or os.environ.get("FPNA_WRITE_APPROVED","")).lower() in ("1","true","yes")
print(tool or "-")
print(model or "-")
print(ver or "-")
print("true" if approved else "false")
PY
)"
TOOL="$(sed -n '1p' <<<"$PARSED")"
TARGET_MODEL="$(sed -n '2p' <<<"$PARSED")"
TARGET_VERSION="$(sed -n '3p' <<<"$PARSED")"
APPROVED="$(sed -n '4p' <<<"$PARSED")"

deny() { echo "[scenario-write-guard] BLOCKED: $1" >&2; exit 2; }

# 1) outright-denied destructive ops (defence in depth; also denied in settings)
case "$TOOL" in
  delete_list_items|run_delete|bulk_delete_models|close_model|set_currentperiod|set_fiscalyear|reset_list_index)
    deny "destructive op '$TOOL' is never permitted from an agent path. Humans only." ;;
esac

# 2) production / actuals target
case "$TARGET_MODEL" in
  *prod*|*production*) deny "write targets a PRODUCTION model ('$TARGET_MODEL'). Point writes at a Dev/sandbox model." ;;
esac
case "$TARGET_VERSION" in
  actual|actuals|current) deny "write targets the '$TARGET_VERSION' version. Writes go to Forecast/Budget/scenario versions only." ;;
esac

# 3) version discipline + approval
if [ "$TARGET_VERSION" = "-" ]; then
  deny "write has no explicit version. Target a Forecast/Budget/named-scenario version on a sandbox model."
fi
case "$TARGET_VERSION" in
  forecast*|budget*|scenario*|whatif*|what-if*|sandbox*) : ;;  # allowed version families
  *) deny "version '$TARGET_VERSION' is not an allowed write target (need Forecast/Budget/scenario)." ;;
esac

if [ "$APPROVED" != "true" ]; then
  deny "write to model='$TARGET_MODEL' version='$TARGET_VERSION' is not approved. Set approved:true (human confirmation in interactive mode, or pre-granted in a signed cron config) before retrying."
fi

exit 0
