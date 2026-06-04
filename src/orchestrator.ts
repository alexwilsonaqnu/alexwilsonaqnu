/**
 * FP&A Chief of Staff — the orchestrator (§5).
 *
 * Routes intent to a capability, enforces effort-scaling, OWNS the provenance
 * ledger, and NEVER computes a number. Retrieval is isolated to the
 * `anaplan-retriever` subagent; every other agent consumes AnaplanFact[].
 *
 * Sprint 0 scope: route to Variance Analysis (§8.1) on the conversational
 * interface. Other routes are declared here as the surface to fill in Phases 1–3.
 */

import { Ledger } from "./ledger/index.js";

export type Capability =
  | "variance" // §8.1  (Sprint 0)
  | "reforecast" // §8.2  (Phase 2)
  | "sensitivity" // §8.3  (Phase 2)
  | "board" // §8.4  (Phase 3)
  | "briefing" // §9.1  (Phase 1)
  | "prep" // §9.3  (Phase 1)
  | "conversational"; // §9.2  default

export interface RouteResult {
  capability: Capability;
  /** subagents this capability delegates to, in order (prompt chaining). */
  pipeline: string[];
  /** whether this route may issue writes (gated, Phase 2+). */
  writes: boolean;
}

/** Intent → capability routing. Read-only routes ship first (Sprint 0 / Phase 1). */
export function route(intent: string): RouteResult {
  const t = intent.toLowerCase();
  if (/\bbriefing\b|morning digest/.test(t))
    return { capability: "briefing", pipeline: ["anaplan-retriever", "narrative-synthesizer"], writes: false };
  if (/\bprep\b|dossier|be prepped|meeting/.test(t))
    return { capability: "prep", pipeline: ["anaplan-retriever", "variance-analyst", "narrative-synthesizer"], writes: false };
  if (/\bboard\b|deck|board pack/.test(t))
    return { capability: "board", pipeline: ["anaplan-retriever", "variance-analyst", "narrative-synthesizer", "board-deck-builder"], writes: false };
  if (/\bsensitiv|tornado|what.?if range/.test(t))
    return { capability: "sensitivity", pipeline: ["anaplan-retriever", "sensitivity-modeler", "narrative-synthesizer"], writes: true };
  if (/\breforecast|re-forecast|roll.?forward/.test(t))
    return { capability: "reforecast", pipeline: ["anaplan-retriever", "reforecaster", "variance-analyst", "narrative-synthesizer"], writes: true };
  if (/\bvariance|vs plan|vs budget|vs forecast|vs prior|bridge|why did/.test(t))
    return { capability: "variance", pipeline: ["anaplan-retriever", "variance-analyst", "narrative-synthesizer"], writes: false };
  return { capability: "conversational", pipeline: ["anaplan-retriever", "narrative-synthesizer"], writes: false };
}

/**
 * Effort-scaling (§5): match work to the question. Reach for autonomous loops last.
 */
export function effort(intent: string): { variants: number; depth: "quick" | "standard" | "deep" } {
  const t = intent.toLowerCase();
  if (/full sweep|all drivers|comprehensive/.test(t)) return { variants: 12, depth: "deep" };
  if (/tornado|top drivers|quick/.test(t)) return { variants: 4, depth: "quick" };
  return { variants: 6, depth: "standard" };
}

/** The orchestrator owns one ledger per session. */
export function openLedger(sessionId?: string): Ledger {
  return new Ledger(sessionId);
}
