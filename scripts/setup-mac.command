#!/bin/bash
# Double-click to enter your credentials via Mac dialog boxes. Writes them to
# .claude/settings.local.json (stays on your computer, never committed).
cd "$(dirname "$0")/.." || exit 1

ask()    { osascript -e "display dialog \"$1\" default answer \"$2\" buttons {\"OK\"} default button 1" -e 'text returned of result' 2>/dev/null; }
hidden() { osascript -e "display dialog \"$1\" default answer \"\" with hidden answer buttons {\"OK\"} default button 1" -e 'text returned of result' 2>/dev/null; }
note()   { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1" 2>/dev/null; }

note "Let's set up your credentials. You'll be asked for 5 things. They are saved only on this Mac."

EMAIL=$(ask "1 of 5 — Your Anaplan login email:" "")
[ -z "$EMAIL" ] && { note "Cancelled."; exit 1; }
PASS=$(hidden "2 of 5 — Your Anaplan password (hidden):")
WS=$(ask "3 of 5 — Anaplan Workspace ID:" "")
MODEL=$(ask "4 of 5 — Anaplan Model ID (use your Dev / sandbox model):" "")
KEY=$(ask "5 of 5 — Anthropic API key (starts with sk-ant-):" "")

AUTH=$(printf '%s' "$EMAIL:$PASS" | base64 | tr -d '\n')

mkdir -p .claude
cat > .claude/settings.local.json <<JSON
{
  "env": {
    "ANAPLAN_BASIC_AUTH": "$AUTH",
    "ANAPLAN_WS_GUID": "$WS",
    "ANAPLAN_MODEL_GUID": "$MODEL",
    "ANTHROPIC_API_KEY": "$KEY"
  }
}
JSON

note "Saved! Now double-click start-mac.command to launch the app."
