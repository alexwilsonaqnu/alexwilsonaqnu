"""Prove every configured model is reachable in THIS project and region before we start.

    python scripts/preflight.py [--verbose]

Fails loudly with remediation rather than letting the first live call surface a 404 in the
middle of a technician's turn. `src/main.py --text` and `--voice` refuse to start unless
this passes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.model_client import ModelGardenConfigError, get_client  # noqa: E402
from src.config import configured_models, gcp_location, gcp_project  # noqa: E402
from src.telemetry import span  # noqa: E402


def check_model(client, model: str) -> tuple[bool, str]:
    """Minimal count_tokens against the tenant — cheapest call that proves reachability."""
    try:
        response = client.models.count_tokens(model=model, contents="ping")
        return True, f"count_tokens ok (total_tokens={getattr(response, 'total_tokens', '?')})"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def remediation(model: str) -> str:
    return (
        f"    -> Enable '{model}' in Model Garden for project {gcp_project()} in region "
        f"{gcp_location()}, confirm the model id is still current (Garden ids churn), and "
        f"check that your ADC principal has roles/aiplatform.user."
    )


def run(verbose: bool = False) -> int:
    print("FieldTech Assist preflight — Model Garden reachability\n")
    try:
        client = get_client()
    except ModelGardenConfigError as exc:
        print("FAIL  environment\n")
        print(f"  {exc}\n")
        print("  Required:")
        print("    export GOOGLE_GENAI_USE_VERTEXAI=true   # or GOOGLE_GENAI_USE_ENTERPRISE=true")
        print("    export GOOGLE_CLOUD_PROJECT=<your-project>")
        print("    export GOOGLE_CLOUD_LOCATION=<region-or-global>")
        print("    gcloud auth application-default login")
        return 1

    print(f"  project : {gcp_project()}")
    print(f"  location: {gcp_location()}\n")

    failures = 0
    with span("preflight", project=gcp_project(), location=gcp_location()) as attrs:
        models = configured_models()
        attrs["models"] = list(models.values())
        for role, model in models.items():
            ok, detail = check_model(client, model)
            status = "PASS" if ok else "FAIL"
            print(f"  {status}  {role:<20} {model}")
            if verbose or not ok:
                print(f"          {detail}")
            if not ok:
                print(remediation(model))
                failures += 1
        attrs["failures"] = failures

    print()
    if failures:
        print(f"{failures} model(s) unreachable. Fix the above before running --text or --voice.")
        return 1
    print("All configured models reachable.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Model Garden reachability.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    return run(verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
