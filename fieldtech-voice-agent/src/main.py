"""Entry point.

    python -m src.main --text
    python -m src.main --voice [--provider fish|elevenlabs]

Both modes drive the same `Orchestrator`. --text exists to develop the brain without
audio; it is not a different agent.
"""

from __future__ import annotations

import argparse
import sys

from src.agent.orchestrator import Orchestrator
from src.config import DEFAULT_VOICE_PROVIDER, HANGUP_PHRASES
from src.telemetry import span


def _preflight_or_exit() -> None:
    from scripts.preflight import run as run_preflight  # test-time import keeps main light

    if run_preflight() != 0:
        print("\nPreflight failed — refusing to start.", file=sys.stderr)
        raise SystemExit(1)
    print()


def _is_hangup(text: str) -> bool:
    lowered = (text or "").strip().lower().rstrip(".!")
    return any(phrase in lowered for phrase in HANGUP_PHRASES)


def run_text() -> int:
    agent = Orchestrator()
    print(
        f"FieldTech Assist — text mode (session {agent.session_id}, "
        f"{agent.llm.provider}/{agent.llm.model})"
    )
    print("Type a technician utterance. Ctrl-D or a hang-up phrase ends the call.\n")
    print(f"agent> {agent.greeting()}\n")
    while True:
        try:
            user = input("tech>  ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[call ended]")
            return 0
        if not user:
            continue
        if _is_hangup(user):
            print("\nagent> Thanks, take care out there.")
            print("[call ended]")
            return 0
        reply = agent.turn(user)
        print(f"\nagent> {reply}\n")


def run_voice(provider_name: str) -> int:
    from src.voice import get_provider
    from src.voice.audio_io import play_wav, record_push_to_talk

    provider = get_provider(provider_name)
    agent = Orchestrator()
    print(f"FieldTech Assist — voice mode (session {agent.session_id}, provider {provider.name})")
    print("Push to talk: Enter to start, Enter to stop. Say 'goodbye' to hang up.\n")

    greeting = agent.greeting()
    print(f"agent> {greeting}")
    play_wav(provider.synthesize(greeting))

    while True:
        try:
            wav = record_push_to_talk()
        except (EOFError, KeyboardInterrupt):
            print("\n[call ended]")
            return 0
        with span("voice.exchange", provider=provider.name, session_id=agent.session_id):
            user = provider.transcribe(wav)
            if not user:
                print("  (nothing heard)")
                continue
            print(f"tech>  {user}")
            if _is_hangup(user):
                farewell = "Thanks, take care out there."
                print(f"agent> {farewell}")
                play_wav(provider.synthesize(farewell))
                print("[call ended]")
                return 0
            reply = agent.turn(user)
            print(f"agent> {reply}")
            play_wav(provider.synthesize(reply))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FieldTech Assist voice agent POC.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--text", action="store_true", help="text REPL (develop the brain)")
    mode.add_argument("--voice", action="store_true", help="push-to-talk voice loop")
    parser.add_argument(
        "--provider", default=DEFAULT_VOICE_PROVIDER, choices=("elevenlabs", "fish")
    )
    args = parser.parse_args(argv)

    _preflight_or_exit()
    return run_text() if args.text else run_voice(args.provider)


if __name__ == "__main__":
    raise SystemExit(main())
