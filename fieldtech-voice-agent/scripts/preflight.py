"""Prove every configured brain is reachable with the credentials you actually have.

    python scripts/preflight.py [--verbose]

Fails loudly with remediation rather than letting the first live call surface a 401 or a
404 in the middle of a technician's turn. `src/main.py --text` and `--voice` refuse to
start unless this passes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.llm import LLMConfigError, configured_clients  # noqa: E402
from src.config import gcp_location, gcp_project, llm_provider  # noqa: E402
from src.telemetry import span  # noqa: E402

_SETUP = {
    "anthropic": [
        "    export ANTHROPIC_API_KEY=sk-ant-...",
        "    # optional: ANTHROPIC_MODEL, ANTHROPIC_EVAL_MODEL, CLAUDE_EFFORT",
    ],
    "gemini": [
        "    export GOOGLE_GENAI_USE_VERTEXAI=true   # or GOOGLE_GENAI_USE_ENTERPRISE=true",
        "    export GOOGLE_CLOUD_PROJECT=<your-project>",
        "    export GOOGLE_CLOUD_LOCATION=<region-or-global>",
        "    gcloud auth application-default login",
    ],
}


def _report_credentials() -> list[str]:
    """Show what actually landed in the environment, masked, and return the names of any
    keys that cannot possibly work. Most 'invalid key' reports are really a key that never
    loaded, an unedited placeholder, or one carrying a stray quote or newline — none of
    which are worth spending an API round trip and a confusing 401 to discover."""
    import os

    watched = ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "FISH_AUDIO_API_KEY")
    blocking: list[str] = []
    print("  credentials seen:")
    for name in watched:
        raw = os.environ.get(name)
        if not raw:
            print(f"    {name:<20} (not set)")
            continue
        key = raw.strip()
        notes = []
        if key != raw:
            notes.append("HAS SURROUNDING WHITESPACE")
        if any(q in key for q in ('"', "'", "“", "”")):
            notes.append("CONTAINS A QUOTE CHARACTER")
            blocking.append(name)
        if "..." in key or "…" in key or key.lower() in ("changeme", "your-key-here"):
            notes.append("STILL THE .env.example PLACEHOLDER")
            blocking.append(name)
        elif len(key) < 20:
            notes.append(f"ONLY {len(key)} CHARS — far too short for a real key")
            blocking.append(name)
        masked = key if len(key) <= 12 else f"{key[:7]}…{key[-4:]}"
        suffix = f"  <- {', '.join(notes)}" if notes else ""
        print(f"    {name:<20} {masked} ({len(key)} chars){suffix}")
    print()

    if blocking:
        names = sorted(set(blocking))
        subject = f"{', '.join(names)} are not usable keys" if len(names) > 1 else f"{names[0]} is not a usable key"
        print(f"  {subject}.\n")
        print("  Paste the real value into .env — bare, no quotes, nothing but the key:")
        print("      ANTHROPIC_API_KEY=sk-ant-api03-<the long string from the console>")
        print("  then reload it:")
        print("      set -a; source .env; set +a\n")
        print("  Only ANTHROPIC_API_KEY is needed for --text; ElevenLabs is for --voice.\n")
    return sorted(set(blocking))


def run(verbose: bool = False) -> int:
    provider = llm_provider()
    print(f"FieldTech Assist preflight — brain: {provider}\n")
    blocking = _report_credentials()
    if provider == "anthropic" and "ANTHROPIC_API_KEY" in blocking:
        # No point spending a round trip to be told 'invalid x-api-key'.
        return 1

    try:
        clients = configured_clients()
    except LLMConfigError as exc:
        print("FAIL  environment\n")
        print(f"  {exc}\n")
        print("  Required:")
        for line in _SETUP.get(provider, []):
            print(line)
        return 1

    if provider == "gemini":
        print(f"  project : {gcp_project()}")
        print(f"  location: {gcp_location()}\n")

    failures = 0
    with span("preflight", provider=provider) as attrs:
        attrs["models"] = [c.model for c in clients.values()]
        for role, client in clients.items():
            try:
                ok, detail = client.preflight()
            except LLMConfigError as exc:
                ok, detail = False, str(exc)
            print(f"  {'PASS' if ok else 'FAIL'}  {role:<20} {client.model}")
            if verbose or not ok:
                print(f"          {detail}")
            if not ok:
                print(client.remediation())
                failures += 1
        attrs["failures"] = failures

    print()
    if failures:
        print(f"{failures} model(s) unreachable. Fix the above before running --text or --voice.")
        return 1
    print("All configured models reachable.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check brain reachability.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    return run(verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
