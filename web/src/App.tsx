import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@ads/react";
import { streamChat, getHealth, type HealthStatus } from "./api";
import type { AnaplanFact, ChatMessage } from "./types";

const SOURCE_LABEL: Record<AnaplanFact["source"], string> = {
  line_item: "Line item",
  sql_calcite: "Calcite SQL",
  scenario_recompute: "Recompute",
  explain_cell: "Explain",
};

const SUGGESTIONS = [
  "What was revenue variance to plan last quarter, and why?",
  "Show gross margin by product family vs prior year.",
  "Why did EMEA margin move in Q3?",
];

/** Presentation only — formats an already-fetched ledger value. No computation. */
function formatValue(v: number | string): string {
  if (typeof v === "number") {
    const abs = Math.abs(v);
    if (abs !== 0 && abs < 1) return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
    return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return v;
}

export default function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [facts, setFacts] = useState<Map<string, AnaplanFact>>(new Map());
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, activity]);

  const factList = useMemo(() => [...facts.values()], [facts]);

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    setActivity(null);
    setMessages((m) => [...m, { role: "user", text: q }, { role: "assistant", text: "" }]);

    await streamChat(q, sessionRef.current, (ev) => {
      switch (ev.type) {
        case "start":
          sessionRef.current = ev.sessionId;
          break;
        case "tool":
          setActivity(ev.name.replace("mcp__anaplan-chimera__", "Anaplan · "));
          break;
        case "token":
          setMessages((m) => {
            const next = [...m];
            const last = next[next.length - 1];
            if (last?.role === "assistant") next[next.length - 1] = { ...last, text: last.text + ev.text };
            return next;
          });
          break;
        case "facts":
          setFacts(() => new Map(ev.facts.map((f) => [f.requestId, f])));
          setMessages((m) => {
            const next = [...m];
            const last = next[next.length - 1];
            if (last?.role === "assistant") next[next.length - 1] = { ...last, factIds: ev.facts.map((f) => f.requestId) };
            return next;
          });
          break;
        case "message":
        case "error":
          setMessages((m) => {
            const next = [...m];
            const last = next[next.length - 1];
            const note = ev.type === "error" ? `⚠️ ${ev.message}` : ev.text;
            if (last?.role === "assistant" && !last.text) next[next.length - 1] = { ...last, text: note };
            else next.push({ role: "assistant", text: note });
            return next;
          });
          break;
        case "done":
          setBusy(false);
          setActivity(null);
          break;
      }
    }).catch((e) => {
      setMessages((m) => [...m, { role: "assistant", text: `⚠️ ${e.message ?? e}` }]);
      setBusy(false);
      setActivity(null);
    });
  }

  return (
    <div className="fpna">
      <header className="fpna__bar">
        <div className="fpna__brand">
          <svg-icon icon="anaplan" class="icon icon--large" />
          <div>
            <strong>FP&amp;A Decision Layer</strong>
            <span className="fpna__sub">Office of the CFO · every number sourced from Anaplan</span>
          </div>
        </div>
        <ReadyPill health={health} />
      </header>

      <main className="fpna__grid">
        <section className="fpna__chat" aria-label="Conversation">
          <div className="fpna__messages" ref={scrollRef}>
            {messages.length === 0 && <Welcome onPick={send} disabled={!health?.ready} />}
            {messages.map((m, i) => (
              <Bubble key={i} msg={m} />
            ))}
            {activity && (
              <div className="fpna__activity">
                <svg-icon icon="sync" class="icon" /> {activity}
              </div>
            )}
          </div>

          <form
            className="fpna__composer"
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
              placeholder={health?.ready ? "Ask about variance, margin, forecast…" : "Configure Anaplan + API key to begin"}
              rows={2}
              disabled={busy || !health?.ready}
            />
            <Button variant="primary" type="submit" disabled={busy || !input.trim() || !health?.ready}>
              {busy ? "Working…" : "Ask"}
            </Button>
          </form>
        </section>

        <aside className="fpna__ledger" aria-label="Provenance ledger">
          <div className="fpna__ledger-head">
            <h2>Provenance ledger</h2>
            <span className="fpna__count">{factList.length} fact{factList.length === 1 ? "" : "s"}</span>
          </div>
          <p className="fpna__ledger-note">
            Every figure in the answer traces to one of these — read from a line item or computed by Anaplan's engine.
          </p>
          <div className="fpna__facts">
            {factList.length === 0 && <div className="fpna__empty">No numbers retrieved yet.</div>}
            {factList.map((f) => (
              <FactCard key={f.requestId} fact={f} />
            ))}
          </div>
        </aside>
      </main>
    </div>
  );
}

function ReadyPill({ health }: { health: HealthStatus | null }) {
  if (!health) return <span className="pill pill--muted">connecting…</span>;
  if (health.ready) return <span className="pill pill--ok">{health.model} · live</span>;
  return <span className="pill pill--warn" title={`Missing: ${health.missing.join(", ")}`}>not configured</span>;
}

function Welcome({ onPick, disabled }: { onPick: (s: string) => void; disabled: boolean }) {
  return (
    <div className="fpna__welcome">
      <h1>Ask the model. Anaplan answers the numbers.</h1>
      <p>The model decides what to fetch and how to say it — it never invents a figure.</p>
      <div className="fpna__chips">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="chip" onClick={() => onPick(s)} disabled={disabled}>
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

function Bubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className={`bubble bubble--${msg.role}`}>
      <div className="bubble__role">{msg.role === "user" ? "You" : "FP&A Chief of Staff"}</div>
      <div className="bubble__text">{msg.text || <span className="bubble__caret" />}</div>
      {msg.factIds && msg.factIds.length > 0 && (
        <div className="bubble__prov">sourced from {msg.factIds.length} ledger fact{msg.factIds.length === 1 ? "" : "s"}</div>
      )}
    </div>
  );
}

function FactCard({ fact }: { fact: AnaplanFact }) {
  const slices = Object.entries(fact.intersection ?? {});
  return (
    <article className="fact">
      <div className="fact__top">
        <span className="fact__value">{formatValue(fact.value)}</span>
        <span className={`fact__src fact__src--${fact.source}`}>{SOURCE_LABEL[fact.source]}</span>
      </div>
      <div className="fact__label">{fact.label}</div>
      <div className="fact__meta">
        <span className="fact__lineitem">{fact.module ? `${fact.module} · ` : ""}{fact.lineItem}</span>
        {fact.version && <span className="fact__ver">{fact.version}</span>}
      </div>
      {slices.length > 0 && (
        <div className="fact__chips">
          {slices.map(([k, v]) => (
            <span key={k} className="fact__chip">
              <em>{k}</em> {v}
            </span>
          ))}
        </div>
      )}
      {fact.query && (
        <details className="fact__query">
          <summary>Calcite query</summary>
          <pre>{fact.query}</pre>
        </details>
      )}
    </article>
  );
}
