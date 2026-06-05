/**
 * The FP&A web backend (live MCP only).
 *
 *   GET  /api/health           → creds/model readiness (no secrets returned)
 *   POST /api/chat             → SSE stream of a single orchestrator turn
 *   GET  /api/facts?session=   → the session's provenance ledger (AnaplanFact[])
 *   GET  /*                    → static frontend (web/dist if built)
 *
 * Lean by design: Node's built-in http + a tiny static handler, zero server
 * framework. The agent harness (MCP, hooks, subagents) comes from `.claude/`.
 */
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, normalize, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Ledger } from "../ledger/index.js";
import { loadLocalEnv, credsStatus } from "./env.js";
import { runTurn } from "./agent.js";

// from src/web/server.ts (or dist/web/server.js) → repo root is two dirs up
const REPO_ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const PORT = Number(process.env.FPNA_WEB_PORT ?? 8787);

// Serve the built ADS app from web/dist if it has been built; otherwise serve
// the zero-dependency built-in UI from public/ (works with no frontend build,
// no private @ads packages — just `npm run web`).
const WEB_DIST = existsSync(join(REPO_ROOT, "web", "dist", "index.html"))
  ? join(REPO_ROOT, "web", "dist")
  : join(REPO_ROOT, "public");

loadLocalEnv(REPO_ROOT);

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
};

function json(res: ServerResponse, status: number, body: unknown): void {
  const s = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(s);
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  return Buffer.concat(chunks).toString("utf8");
}

function sse(res: ServerResponse): (event: string, data: unknown) => void {
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
  });
  return (event, data) => {
    res.write(`event: ${event}\n`);
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };
}

async function serveStatic(res: ServerResponse, urlPath: string): Promise<void> {
  let rel = decodeURIComponent(urlPath.split("?")[0] ?? "/");
  if (rel === "/" || rel === "") rel = "/index.html";
  // contain within WEB_DIST
  const filePath = normalize(join(WEB_DIST, rel));
  if (!filePath.startsWith(WEB_DIST)) {
    res.writeHead(403).end("forbidden");
    return;
  }
  try {
    const info = await stat(filePath);
    const target = info.isDirectory() ? join(filePath, "index.html") : filePath;
    const buf = await readFile(target);
    res.writeHead(200, { "content-type": MIME[extname(target)] ?? "application/octet-stream" });
    res.end(buf);
  } catch {
    // SPA fallback to index.html if the build exists, else a friendly hint
    try {
      const buf = await readFile(join(WEB_DIST, "index.html"));
      res.writeHead(200, { "content-type": MIME[".html"] }).end(buf);
    } catch {
      res.writeHead(200, { "content-type": MIME[".html"] }).end(
        `<!doctype html><meta charset=utf-8><body style="font-family:system-ui;padding:2rem;max-width:42rem">
        <h1>FP&A Decision Layer — backend running</h1>
        <p>The frontend isn't built yet. From <code>web/</code> run <code>pnpm install && pnpm build</code>
        (or <code>pnpm dev</code> for the Vite dev server, which proxies <code>/api</code> here on :${PORT}).</p>
        <p>Health: <a href="/api/health">/api/health</a></p></body>`,
      );
    }
  }
}

const server = createServer(async (req, res) => {
  const url = req.url ?? "/";
  try {
    if (url.startsWith("/api/health")) {
      const { ready, missing, surface } = credsStatus();
      return json(res, 200, { ready, missing, surface, model: process.env.FPNA_MODEL ?? "claude-sonnet-4-6" });
    }

    if (url.startsWith("/api/debug")) {
      // last raw Anaplan tool response, for tuning the ledger parser
      try {
        const buf = await readFile(join(REPO_ROOT, ".fpna", "debug", "last-tool-response.json"), "utf8");
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        return res.end(buf);
      } catch {
        return json(res, 200, { note: "No tool response captured yet. Ask a question first, then reload this page." });
      }
    }

    if (url.startsWith("/api/facts")) {
      const session = new URL(url, "http://x").searchParams.get("session") ?? "";
      const facts = session ? new Ledger(session).all() : [];
      return json(res, 200, { sessionId: session, facts });
    }

    if (url.startsWith("/api/chat") && req.method === "POST") {
      const body = JSON.parse((await readBody(req)) || "{}") as { sessionId?: string; message?: string };
      const message = (body.message ?? "").trim();
      const sessionId = body.sessionId || `web_${Date.now().toString(36)}`;
      if (!message) return json(res, 400, { error: "message is required" });

      const { ready, missing } = credsStatus();
      if (!ready) return json(res, 412, { error: "not configured", missing });

      const send = sse(res);
      send("start", { sessionId });
      req.on("close", () => res.end());
      for await (const ev of runTurn(message, sessionId, REPO_ROOT)) {
        send(ev.type, ev);
        if (ev.type === "done") break;
      }
      return res.end();
    }

    if (req.method === "GET") return serveStatic(res, url);
    res.writeHead(405).end("method not allowed");
  } catch (err) {
    json(res, 500, { error: err instanceof Error ? err.message : String(err) });
  }
});

server.listen(PORT, () => {
  const { ready, missing } = credsStatus();
  console.log(`▶ FP&A web backend on http://localhost:${PORT}`);
  console.log(ready ? "  ✓ credentials present" : `  ! not configured — missing: ${missing.join(", ")}`);
  console.log(`  health: http://localhost:${PORT}/api/health`);
});
