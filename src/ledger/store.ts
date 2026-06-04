import { appendFileSync, mkdirSync, readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { randomUUID } from "node:crypto";
import type { AnaplanFact } from "./fact.js";

/**
 * The provenance ledger (§7). Run-scoped, append-only JSONL at
 * `.fpna/ledger/<session>.jsonl`. This is the allowlist of numbers the system
 * may speak. Written by the ledger-append hook and (in-process) by the SDK;
 * read by the orchestrator, narrative-synthesizer, board-deck-builder, and the
 * provenance Stop-gate.
 *
 * The on-disk format MUST match what `.claude/hooks/ledger-append.py` writes so
 * the Python hook and the TypeScript runtime share one source of truth.
 */

export const LEDGER_ROOT = process.env.FPNA_LEDGER_DIR ?? ".fpna/ledger";

export function ledgerPath(sessionId: string): string {
  return join(LEDGER_ROOT, `${sessionId}.jsonl`);
}

export class Ledger {
  readonly sessionId: string;
  readonly path: string;

  constructor(sessionId?: string) {
    this.sessionId = sessionId ?? process.env.FPNA_SESSION_ID ?? randomUUID();
    this.path = ledgerPath(this.sessionId);
  }

  /** Append a fact. Stamps requestId/fetchedAt if missing. Returns the fact. */
  append(
    fact: Omit<AnaplanFact, "requestId" | "fetchedAt"> &
      Partial<Pick<AnaplanFact, "requestId" | "fetchedAt">>,
  ): AnaplanFact {
    const full: AnaplanFact = {
      ...fact,
      requestId: fact.requestId ?? `req_${randomUUID().slice(0, 8)}`,
      fetchedAt: fact.fetchedAt ?? new Date().toISOString(),
    };
    mkdirSync(dirname(this.path), { recursive: true });
    appendFileSync(this.path, JSON.stringify(full) + "\n", "utf8");
    return full;
  }

  /** Read every fact retrieved this session. */
  all(): AnaplanFact[] {
    if (!existsSync(this.path)) return [];
    return readFileSync(this.path, "utf8")
      .split("\n")
      .filter((l) => l.trim().length > 0)
      .map((l) => JSON.parse(l) as AnaplanFact);
  }

  byRequestId(requestId: string): AnaplanFact | undefined {
    return this.all().find((f) => f.requestId === requestId);
  }
}
