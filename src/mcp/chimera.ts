/**
 * Chimera MCP client config (§2A) — the deterministic numeric brain (PRIMARY).
 *
 * Connection is Streamable HTTP via `mcp-remote`. Auth/GUIDs are injected from
 * the environment (settings.local.json), NEVER hardcoded or logged (§11).
 * Every write must point at a Dev/sandbox model.
 */

export interface ChimeraEnv {
  basicAuth: string; // base64(username:password)
  workspaceGuid: string;
  modelGuid: string; // DEV/SANDBOX only
}

export function chimeraEnv(): ChimeraEnv {
  const basicAuth = process.env.ANAPLAN_BASIC_AUTH;
  const workspaceGuid = process.env.ANAPLAN_WS_GUID;
  const modelGuid = process.env.ANAPLAN_MODEL_GUID;
  if (!basicAuth || !workspaceGuid || !modelGuid) {
    throw new Error(
      "Missing Anaplan Chimera credentials. Set ANAPLAN_BASIC_AUTH, " +
        "ANAPLAN_WS_GUID, ANAPLAN_MODEL_GUID in .claude/settings.local.json (gitignored).",
    );
  }
  return { basicAuth, workspaceGuid, modelGuid };
}

export const CHIMERA_ENDPOINT =
  "https://us1a.app-chimera.anaplan.com/internal-api-gateway-core/modeling-mcp-service/mcp";

/** mcp-remote argv for the Chimera server (mirrors .claude/settings.json). */
export function chimeraMcpArgs(): string[] {
  // Values are resolved by the harness from env at launch; we never embed them.
  return [
    "mcp-remote",
    CHIMERA_ENDPOINT,
    "--header",
    "Authorization: Basic ${ANAPLAN_BASIC_AUTH}",
    "--header",
    "x-workspace-guid: ${ANAPLAN_WS_GUID}",
    "--header",
    "x-model-guid: ${ANAPLAN_MODEL_GUID}",
    "--header",
    "x-traceid: fpna-agents",
    "--header",
    "x-tracepath: fpna-agents",
  ];
}

/**
 * A fully-resolved stdio MCP server config for the SDK `mcpServers` option.
 *
 * Unlike `.claude/settings.json` (where the harness expands `${VAR}` headers),
 * the SDK takes the args verbatim — so we substitute the real credential values
 * from the environment here. Throws if creds are missing.
 */
export function chimeraServerConfig(): {
  type: "stdio";
  command: string;
  args: string[];
} {
  const { basicAuth, workspaceGuid, modelGuid } = chimeraEnv();
  return {
    type: "stdio",
    command: "npx",
    args: [
      "-y",
      "mcp-remote",
      CHIMERA_ENDPOINT,
      "--header",
      `Authorization: Basic ${basicAuth}`,
      "--header",
      `x-workspace-guid: ${workspaceGuid}`,
      "--header",
      `x-model-guid: ${modelGuid}`,
      "--header",
      "x-traceid: fpna-agents",
      "--header",
      "x-tracepath: fpna-agents",
    ],
  };
}

/** Tools the retriever is allowed to call on this surface (read/explain only). */
export const CHIMERA_READ_TOOLS = [
  "aocfo_catalog_modules",
  "aocfo_catalog_line_items",
  "aocfo_catalog_lists",
  "aocfo_catalog_properties",
  "aocfo_sql_schema",
  "aocfo_sql_query",
  "aocfo_explain_cell",
  "aocfo_get_model_context",
] as const;
