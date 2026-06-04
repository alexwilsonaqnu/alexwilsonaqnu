#!/usr/bin/env python3
"""
Inject (or reset) the Anaplan Basic-auth header in the Claude Desktop config.

Why this exists:
  The Authorization header is base64(username:password) -- effectively your
  password in plaintext. This script builds it from a hidden getpass prompt so
  the secret never appears in your terminal, shell history, command args, or any
  chat transcript. The only place it lands is claude_desktop_config.json itself
  (which is how the MCP server reads it).

Usage:
  python3 anaplan_auth.py            # inject creds into the placeholder
  python3 anaplan_auth.py --reset    # wipe the current auth back to a placeholder
                                      # (use before re-injecting new credentials)

After running, fully quit Claude Desktop (Cmd+Q) and reopen it.
"""
import sys, json, os, base64, getpass

CONFIG = os.path.expanduser(
    "~/Library/Application Support/Claude/claude_desktop_config.json"
)
SERVER = "anaplan-chimera"
PLACEHOLDER = "###BASE_64_AUTH_STRING###"
AUTH_PREFIX = "Authorization: Basic "


def load():
    with open(CONFIG) as f:
        return json.load(f)


def save(d):
    with open(CONFIG, "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")


def reset():
    d = load()
    args = d["mcpServers"][SERVER]["args"]
    d["mcpServers"][SERVER]["args"] = [
        AUTH_PREFIX + PLACEHOLDER if a.startswith(AUTH_PREFIX) else a
        for a in args
    ]
    save(d)
    print(f"🔄 Auth reset to placeholder. Re-run without --reset to inject new creds.")


def inject():
    d = load()
    args = d["mcpServers"][SERVER]["args"]
    if not any(PLACEHOLDER in a for a in args):
        print("⚠️  No placeholder found — auth looks already injected.")
        print("    Run with --reset first if you want to replace the credentials.")
        return
    user = input("Anaplan username (email): ").strip()
    pw = getpass.getpass("Anaplan password (hidden): ")
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    d["mcpServers"][SERVER]["args"] = [a.replace(PLACEHOLDER, auth) for a in args]
    save(d)
    print(f"✅ Auth header injected (length {len(auth)}).")
    print("   Now fully quit Claude Desktop (Cmd+Q) and reopen it.")


if __name__ == "__main__":
    if not os.path.exists(CONFIG):
        sys.exit(f"Config not found: {CONFIG}")
    if "--reset" in sys.argv:
        reset()
    else:
        inject()
