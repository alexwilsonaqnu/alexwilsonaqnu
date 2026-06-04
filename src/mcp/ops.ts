/**
 * anaplan-ops MCP surface (§2B) — the operational REST-v2 wrapper (70 tools).
 *
 * Unlike Chimera (internal endpoint, Basic auth, mcp-remote stdio), this is a
 * PUBLIC Azure Container Apps endpoint over Streamable HTTP with a Bearer token —
 * reachable without the Anaplan VPN and with no subprocess. That makes it the
 * robust choice for the web app. Every tool takes workspaceId + modelId as
 * parameters (no separate model-binding step).
 *
 * We expose only the read surface here; mutations/destructive ops are denied in
 * permissions.ts (and never wired into the web path).
 */

export const OPS_ENDPOINT =
  "https://anaplan-mcp.kindbush-f3ce94c7.eastus.azurecontainerapps.io/mcp";

export function opsToken(): string | undefined {
  const t = process.env.ANAPLAN_OPS_TOKEN;
  return t && !t.startsWith("<") ? t : undefined;
}

export function opsServerConfig(): { type: "http"; url: string; headers: Record<string, string> } {
  const token = opsToken();
  if (!token) {
    throw new Error("Missing ANAPLAN_OPS_TOKEN. Set it in .claude/settings.local.json (or run setup).");
  }
  return { type: "http", url: OPS_ENDPOINT, headers: { Authorization: `Bearer ${token}` } };
}

/** Read/explore tools allowed in the web path (no data mutation). */
export const OPS_READ_RE =
  /^mcp__anaplan-ops__(show_[a-z]+|read_cells|run_export|get_[a-z_]+|create_(view|list)_readrequest|delete_(view|list)_readrequest|lookup_dimensionitems|preview_list|open_model)$/;

/** Mutations / destructive / config — always denied. */
export const OPS_DENY_RE =
  /^mcp__anaplan-ops__(write_cells|add_list_items|update_list_items|delete_list_items|run_import|run_process|run_delete|upload_file|delete_file|close_model|bulk_delete_models|set_currentperiod|set_fiscalyear|set_versionswitchover|reset_list_index|cancel_task|download_file)$/;
