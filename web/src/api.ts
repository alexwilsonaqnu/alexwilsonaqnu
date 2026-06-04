import type { AgentEvent, AnaplanFact } from "./types";

export interface HealthStatus {
  ready: boolean;
  missing: string[];
  model: string;
}

export async function getHealth(): Promise<HealthStatus> {
  const res = await fetch("/api/health");
  return res.json();
}

export async function getFacts(sessionId: string): Promise<AnaplanFact[]> {
  const res = await fetch(`/api/facts?session=${encodeURIComponent(sessionId)}`);
  const body = (await res.json()) as { facts: AnaplanFact[] };
  return body.facts ?? [];
}

/**
 * Stream one orchestrator turn. EventSource is GET-only, so we POST and parse
 * the SSE framing off the fetch body stream ourselves.
 */
export async function streamChat(
  message: string,
  sessionId: string | null,
  onEvent: (ev: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, sessionId }),
    signal,
  });

  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    onEvent({ type: "error", message: err.error ?? `HTTP ${res.status}` });
    if (err.missing) onEvent({ type: "error", message: `Missing config: ${err.missing.join(", ")}` });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line; each has `event:` and `data:` lines.
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      try {
        onEvent(JSON.parse(dataLine.slice(5).trim()) as AgentEvent);
      } catch {
        /* ignore malformed frame */
      }
    }
  }
}
