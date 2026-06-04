#!/usr/bin/env bash
# Bootstrap the FP&A agents workspace (§10).
# - ensures the ledger/backup dirs exist
# - validates settings.local.json is present (secrets) without printing it
# - sanity-checks the toolchain the hooks depend on
set -euo pipefail
cd "$(dirname "$0")"

echo "▶ fpna-agents init"

mkdir -p .fpna/ledger .fpna/backups outputs

# toolchain the hooks rely on
for bin in python3 node bash; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "  ✗ missing required tool: $bin" >&2; exit 1
  fi
done
echo "  ✓ toolchain: python3 $(python3 -V 2>&1 | awk '{print $2}'), node $(node -v), bash"

# hooks must be executable
chmod +x .claude/hooks/*.py .claude/hooks/*.sh
echo "  ✓ hooks executable"

# secrets present? (do not print them)
if [ -f .claude/settings.local.json ]; then
  echo "  ✓ settings.local.json present"
else
  echo "  ! settings.local.json missing — copy .claude/settings.local.json.example and fill GUIDs/auth"
fi

# OTel endpoint hint (§12) — non-fatal
: "${OTEL_EXPORTER_OTLP_ENDPOINT:=http://localhost:4317}"
echo "  · OTel OTLP endpoint: ${OTEL_EXPORTER_OTLP_ENDPOINT}"

echo "▶ done. Run: npm install && npm test"
