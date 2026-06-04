#!/bin/bash
# Double-click to launch the FP&A Decision Layer web app.
# First run: installs Node deps and (if needed) runs setup. Then opens your browser.
cd "$(dirname "$0")/.." || exit 1
clear
echo "▶ FP&A Decision Layer — starting up…"
echo

note() { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1" 2>/dev/null; }

# 1. Node.js present?
if ! command -v node >/dev/null 2>&1; then
  note "Node.js isn't installed yet. I'll open the download page — install the LTS version (just click through the installer), then double-click start-mac.command again."
  open "https://nodejs.org/en/download/"
  exit 1
fi

# 2. Credentials present?
if [ ! -f .claude/settings.local.json ]; then
  note "No credentials found yet — let's set those up first."
  bash "scripts/setup-mac.command"
  [ ! -f .claude/settings.local.json ] && exit 1
fi

# 3. Dependencies installed?
if [ ! -d node_modules ]; then
  echo "Installing dependencies (first run only, ~1 minute)…"
  if ! npm install; then
    note "Installing dependencies failed. Open Terminal in this folder and run: npm install"
    exit 1
  fi
fi

# 4. Launch + open browser
PORT="${FPNA_WEB_PORT:-8787}"
echo "Opening http://localhost:$PORT in your browser…"
echo "(Leave this window open while you use the app. Close it to stop.)"
( sleep 4; open "http://localhost:$PORT" ) &
npm run web
