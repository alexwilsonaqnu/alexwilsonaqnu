#!/usr/bin/env bash
# PreCompact → backup-ledger (§7).
# The ledger is the allowlist of speakable numbers; it MUST survive context loss.
# Back up the run-scoped ledger and transcript before the harness compacts.
set -euo pipefail

INPUT="$(cat)"

read -r SESSION TRANSCRIPT <<<"$(
python3 - "$INPUT" <<'PY'
import json, os, sys
try:
    p = json.loads(sys.argv[1])
except Exception:
    print("- -"); raise SystemExit
print(p.get("session_id") or os.environ.get("FPNA_SESSION_ID","-"),
      p.get("transcript_path") or "-")
PY
)"

LEDGER_DIR="${FPNA_LEDGER_DIR:-.fpna/ledger}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST=".fpna/backups/${SESSION}/${STAMP}"
mkdir -p "$DEST"

if [ "$SESSION" != "-" ] && [ -f "${LEDGER_DIR}/${SESSION}.jsonl" ]; then
  cp "${LEDGER_DIR}/${SESSION}.jsonl" "${DEST}/ledger.jsonl"
fi
if [ "$TRANSCRIPT" != "-" ] && [ -f "$TRANSCRIPT" ]; then
  cp "$TRANSCRIPT" "${DEST}/transcript.jsonl"
fi

echo "[backup-ledger] backed up session '${SESSION}' to ${DEST}"
exit 0
