# Connect the Anaplan Chimera MCP server to Claude Desktop (macOS)

This packages the internal **Anaplan Chimera Dev** modeling MCP server so you can
drive your Anaplan model from **Claude Desktop**. ~5 minutes.

> Internal use only — this points at internal Anaplan infrastructure and uses your
> own Anaplan Chimera Dev login. Don't distribute outside Anaplan.

## What's in here

| File | Purpose |
|---|---|
| `claude_desktop_config.template.json` | The MCP server block to add to your Claude config |
| `anaplan_auth.py` | Safely injects your Basic-auth header (hidden password prompt) |
| `README.md` | This file |

---

## Prerequisites

1. **Access to a Workspace/Model in Anaplan Chimera Dev.** You should have received
   email invites and set a password. From that environment, grab your
   **workspace GUID** and **model GUID** (you'll plug them in below).
2. **Node.js / npx.** Open Terminal and run:
   ```zsh
   npx -v
   ```
   If that errors, install Node from https://nodejs.org/en/download and re-check.
3. **Claude Desktop** installed.

---

## Step 1 — Add the server to your Claude config

Open your Claude Desktop config:

- **Claude Desktop → Settings → Developer → Edit Config**, or open directly:
  ```
  ~/Library/Application Support/Claude/claude_desktop_config.json
  ```

**Merge** the `mcpServers` block from `claude_desktop_config.template.json` into your
config. If the file already has a `mcpServers` section, add `anaplan-chimera` inside
it; otherwise paste the whole `mcpServers` block. **Don't delete your existing
settings.**

Then fill in **your** GUIDs (from your Chimera Dev environment):

```json
"x-workspace-guid: <YOUR_WORKSPACE_GUID>",
...
"x-model-guid: <YOUR_MODEL_GUID>",
```

Leave `Authorization: Basic ###BASE_64_AUTH_STRING###` exactly as-is — the next step
fills it in.

> ⚠️ **Use straight quotes only** (`"`). If you retype any line, watch out for
> "smart"/curly quotes (`"` `"`) — they make the JSON invalid and Claude will
> silently skip the server. Validate with:
> ```zsh
> python3 -c "import json; json.load(open('$HOME/Library/Application Support/Claude/claude_desktop_config.json')); print('valid')"
> ```

## Step 2 — Inject your credentials (safely)

Your auth header is `base64(username:password)` — effectively your password. **Do not**
type it by hand or paste it anywhere. Run the helper instead (from this folder):

```zsh
python3 anaplan_auth.py
```

- Enter your Anaplan username (email) when asked.
- Enter your password at the **hidden** prompt (you won't see characters — that's
  normal).
- It base64-encodes `username:password` and writes it into your Claude config.

The password is read with Python's `getpass` — it never appears on screen, in your
shell history, in command arguments, or anywhere except the config file itself.

To change credentials later:
```zsh
python3 anaplan_auth.py --reset   # blank the auth back to a placeholder
python3 anaplan_auth.py           # inject new credentials
```

## Step 3 — Reload Claude Desktop

1. **Fully quit** with `Cmd+Q` (just closing the window leaves the MCP process
   running — changes won't apply).
2. Reopen Claude Desktop.
3. **Settings → Developer** → `anaplan-chimera` should show as **running**.
   (The first request may take a few seconds while `npx` downloads `mcp-remote`.)

## Step 4 — Try it

In a chat:

> "List your anaplan-chimera tools" — or — "Summarise the modules in my Anaplan model."

The server provides ~58 tools to read **and modify** your model (modules, lists,
line items, formulae, versions, time, cells, etc.). It writes to the live model named
by your `x-model-guid` — **keep it on a Dev model.**

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Server missing / silently ignored | Invalid JSON — usually smart/curly quotes. Replace with straight `"` and validate (Step 1). |
| `read: -p: no coprocess` | You tried a bash one-liner in zsh. Just use `python3 anaplan_auth.py` — it avoids this. |
| `IndentationError` / stuck at `heredoc>` | Don't paste long multi-line commands into Terminal — they wrap and break. Use the script. |
| 401 / auth error in Developer tab | Wrong username/password: `python3 anaplan_auth.py --reset` then re-run to re-enter. |
| Stuck "connecting" | `npx` fetching `mcp-remote` on first run (~30s). If it never settles, check network/VPN/proxy. |
| Changes not applying | You didn't fully quit. `Cmd+Q`, then reopen. |

---

## Security notes

- This package contains **no credentials** — only a placeholder. Your real auth
  string lives solely in your own `claude_desktop_config.json`.
- **Never share your `claude_desktop_config.json`** after running Step 2 — it contains
  your reversible Basic-auth (password). Share *this package*, not your config.
- Everyone uses their **own** Anaplan login and their **own** workspace/model GUIDs.
