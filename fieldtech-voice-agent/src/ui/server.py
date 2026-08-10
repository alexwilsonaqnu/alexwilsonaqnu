"""Demo UI — the technician's app surface, and a push-to-talk handset.

    python -m src.main --ui        (or: python -m src.ui.server)

Deliberately stdlib-only: no Flask, no FastAPI, no uvicorn. A demo you can't install on
a locked-down laptop is not a demo, and `pip install -r requirements.txt` should stay the
whole setup.

This is a *surface*, exactly like the CLI: it calls the same Orchestrator.turn() and
holds no repair logic of its own. Figures reach the browser the same way they would reach
a real app — the agent calls get_figure, which appends a delivery record, and this server
forwards records that appeared during the turn.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.agent.orchestrator import Orchestrator
from src.config import FIGURE_PUSHES_PATH, FIGURES_DIR, SALESFORCE_FIXTURE_PATH
from src.telemetry import span

STATIC_DIR = Path(__file__).resolve().parent / "static"

_lock = threading.Lock()  # Orchestrator holds conversation state; serialize turns.
_agent: Orchestrator | None = None
_session_push_start = 0  # deliveries already in the log before this session began
_voice: Any = None
_voice_error: str | None = None


def _get_agent() -> Orchestrator:
    global _agent, _session_push_start
    if _agent is None:
        _session_push_start = len(_pushes())
        _agent = Orchestrator()
    return _agent


def _get_voice():
    """Voice is optional: the UI still runs in text mode without a speech key."""
    global _voice, _voice_error
    if _voice is not None or _voice_error is not None:
        return _voice
    try:
        from src.config import DEFAULT_VOICE_PROVIDER
        from src.voice import get_provider

        _voice = get_provider(DEFAULT_VOICE_PROVIDER)
    except Exception as exc:
        _voice_error = f"{type(exc).__name__}: {exc}"
    return _voice


# --- figure deliveries -------------------------------------------------------
def _pushes() -> list[dict[str, Any]]:
    if not FIGURE_PUSHES_PATH.exists():
        return []
    records = []
    with FIGURE_PUSHES_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _run_turn(text: str) -> dict[str, Any]:
    """One turn, plus whatever figures the agent pushed during it."""
    before = len(_pushes())
    with _lock:
        agent = _get_agent()
        reply = agent.turn(text)
        after = _pushes()
    return {
        "reply": reply,
        "figures": _as_figures(after[before:]),
        "model_number": agent.model_number,
        "case_id": agent.case_id,
    }


def _as_figures(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "doc_id": r["doc_id"],
            "figure_id": r["figure_id"],
            "delivered_at": r.get("delivered_at"),
        }
        for r in records
    ]


def _demo_card() -> dict[str, Any]:
    """The identifiers a presenter needs on screen, read from the fixture itself so the
    card can never drift from what salesforce_lookup will actually return."""
    with SALESFORCE_FIXTURE_PATH.open(encoding="utf-8") as fh:
        techs = json.load(fh).get("technicians", [])
    with_case = next((t for t in techs if t.get("open_case")), None)
    without = next((t for t in techs if not t.get("open_case")), None)
    card: dict[str, Any] = {}
    if with_case:
        case = with_case["open_case"]
        card["technician_id"] = with_case["technician_id"]
        card["technician_name"] = with_case["name"]
        card["model_number"] = case["model_number"]
        card["serial_number"] = case["serial_number"]
        card["case_id"] = case["case_id"]
        card["issue"] = case["issue_summary"]
    if without:
        card["unknown_technician_id"] = without["technician_id"]
    return card


class Handler(BaseHTTPRequestHandler):
    server_version = "FieldTechAssist/0.1"

    def log_message(self, fmt, *args):  # quieter console during a demo
        pass

    # -- helpers -------------------------------------------------------------
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict[str, Any], code: int = 200) -> None:
        self._send(code, json.dumps(payload, default=str).encode(), "application/json")

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    # -- routes --------------------------------------------------------------
    def do_GET(self) -> None:
        route = urlparse(self.path)
        if route.path in ("/", "/index.html"):
            return self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")

        if route.path == "/api/state":
            agent = _get_agent()
            return self._json(
                {
                    "session_id": agent.session_id,
                    "provider": agent.llm.provider,
                    "model": agent.llm.model,
                    "greeting": agent.greeting(),
                    "voice_available": _get_voice() is not None,
                    "voice_error": _voice_error,
                    "demo": _demo_card(),
                    # Rehydrate the app panel: a reload mid-call must not lose the
                    # diagrams the technician was already sent.
                    "figures": _as_figures(_pushes()[_session_push_start:]),
                }
            )

        if route.path == "/api/figure":
            params = parse_qs(route.query)
            figure_id = (params.get("figure_id") or [""])[0]
            # Resolve inside FIGURES_DIR and reject anything that escapes it — figure_id
            # reaches us from the browser.
            candidate = (FIGURES_DIR / f"{figure_id}.png").resolve()
            try:
                candidate.relative_to(FIGURES_DIR.resolve())
            except ValueError:
                return self._json({"error": "bad figure id"}, 400)
            if not candidate.exists():
                return self._json({"error": "not found"}, 404)
            return self._send(200, candidate.read_bytes(), "image/png")

        return self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        route = urlparse(self.path)

        if route.path == "/api/text":
            try:
                text = (json.loads(self._body() or b"{}").get("text") or "").strip()
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)
            if not text:
                return self._json({"error": "empty"}, 400)
            with span("ui.turn", mode="text"):
                return self._json(_run_turn(text))

        if route.path == "/api/audio":
            voice = _get_voice()
            if voice is None:
                return self._json({"error": f"Voice unavailable: {_voice_error}"}, 503)
            audio = self._body()
            if not audio:
                return self._json({"error": "no audio"}, 400)
            mime = self.headers.get("Content-Type") or "audio/webm"
            with span("ui.turn", mode="voice", audio_bytes=len(audio)) as attrs:
                transcript = voice.transcribe(audio, mime_type=mime)
                attrs["transcript_chars"] = len(transcript)
                if not transcript:
                    return self._json({"transcript": "", "reply": "", "figures": []})
                result = _run_turn(transcript)
                result["transcript"] = transcript
                try:
                    wav = voice.synthesize(result["reply"])
                    result["audio"] = base64.b64encode(wav).decode()
                except Exception as exc:  # speech failure must not lose the text reply
                    result["audio"] = None
                    result["tts_error"] = f"{type(exc).__name__}: {exc}"
                return self._json(result)

        return self._json({"error": "not found"}, 404)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    mimetypes.init()
    agent = _get_agent()
    card = _demo_card()
    print(f"FieldTech Assist — demo UI  http://{host}:{port}")
    print(f"  session {agent.session_id} · {agent.llm.provider}/{agent.llm.model}")
    print(f"  voice: {'ready' if _get_voice() else f'text only ({_voice_error})'}")
    print(f"\n  Demo: technician {card.get('technician_id')} · model {card.get('model_number')}\n")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FieldTech Assist demo UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    serve(args.host, args.port)
