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


def run(verbose: bool = False) -> int:
    provider = llm_provider()
    print(f"FieldTech Assist preflight — brain: {provider}\n")

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
