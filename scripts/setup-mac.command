#!/bin/bash
# Double-click to enter your credentials via Mac dialog boxes. Writes them to
# .claude/settings.local.json (stays on your computer, never committed).
cd "$(dirname "$0")/.." || exit 1

ask()    { osascript -e "display dialog \"$1\" default answer \"$2\" buttons {\"OK\"} default button 1" -e 'text returned of result' 2>/dev/null; }
hidden() { osascript -e "display dialog \"$1\" default answer \"\" with hidden answer buttons {\"OK\"} default button 1" -e 'text returned of result' 2>/dev/null; }
note()   { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1" 2>/dev/null; }

note "Let's set up your credentials. Saved only on this Mac. The first 4 are required; the last 2 are optional."

KEY=$(ask "1 of 6 — Anthropic API key (starts with sk-ant-):" "")
WS=$(ask "2 of 6 — Anaplan Workspace ID:" "")
MODEL=$(ask "3 of 6 — Anaplan Model ID (your Dev / sandbox model):" "")
OPS=$(ask "4 of 6 — Anaplan ops Bearer token (the public server — no VPN needed):" "")

note "Optional: the next 2 are only for the internal Chimera SQL server (needs Anaplan VPN). Leave blank to skip."
EMAIL=$(ask "5 of 6 — (optional) Anaplan login email — for the Chimera VPN server:" "")
PASS=$(hidden "6 of 6 — (optional) Anaplan password — for the Chimera VPN server:")

AUTH=""
[ -n "$EMAIL" ] && AUTH=$(printf '%s' "$EMAIL:$PASS" | base64 | tr -d '\n')

mkdir -p .claude
cat > .claude/settings.local.json <<JSON
{
  "env": {
    "ANTHROPIC_API_KEY": "$KEY",
    "ANAPLAN_WS_GUID": "$WS",
    "ANAPLAN_MODEL_GUID": "$MODEL",
    "ANAPLAN_OPS_TOKEN": "$OPS",
    "ANAPLAN_BASIC_AUTH": "$AUTH"
  }
}
JSON

note "Saved! Now double-click start-mac.command to launch the app."
