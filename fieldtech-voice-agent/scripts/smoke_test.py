"""Keyless smoke tests for the seams that do not need a model.

    python scripts/smoke_test.py

Covers verification gate 4: figure delivery, the ServiceMatters offline fallback, and the
pre-hook actually raising on a simulated write into evals/. Exits nonzero on any failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent import hooks  # noqa: E402
from src.agent.dispatcher import dispatch  # noqa: E402
from src.config import CHUNKS_PATH, FIGURE_PUSHES_PATH  # noqa: E402
from src.guards import ProtectedPathError, guard_write_path  # noqa: E402
from src.tools.manual_search import load_chunks  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}" + (f"\n          {detail}" if detail else ""))
        failures.append(name)


def main() -> int:
    print("Smoke tests (no model calls, no API keys)\n")

    # --- get_figure returns the extracted PNG and logs the push --------------
    chunks = load_chunks()
    figure_chunk = next((c for c in chunks if c.get("figures")), None)
    if figure_chunk is None:
        check("get_figure", False, f"no ingested chunk carries a figure ({CHUNKS_PATH})")
    else:
        figure_id = figure_chunk["figures"][0]
        before = FIGURE_PUSHES_PATH.stat().st_size if FIGURE_PUSHES_PATH.exists() else 0
        result = dispatch("get_figure", {"doc_id": figure_chunk["doc_id"], "figure_id": figure_id})
        png_ok = result.get("delivered") and Path(result["figure_path"]).exists()
        check("get_figure returns the extracted PNG", bool(png_ok), json.dumps(result))
        after = FIGURE_PUSHES_PATH.stat().st_size if FIGURE_PUSHES_PATH.exists() else 0
        check("get_figure logs the delivery", after > before, str(FIGURE_PUSHES_PATH))

    # --- service_matters_search falls back to the local registry offline -----
    # Point the tool at an unroutable host to force the failure path.
    import os

    previous = os.environ.get("SERVICE_MATTERS_URL")
    os.environ["SERVICE_MATTERS_URL"] = "http://127.0.0.1:9/search"
    try:
        stage1 = dispatch("service_matters_search", {"model_number": "WTW5057LW0"})
    finally:
        if previous is None:
            os.environ.pop("SERVICE_MATTERS_URL", None)
        else:
            os.environ["SERVICE_MATTERS_URL"] = previous
    check(
        "service_matters_search falls back to the local registry",
        stage1.get("fell_back") and stage1.get("source") == "local_doc_registry",
        json.dumps(stage1),
    )
    check(
        "fallback still returns candidate doc_ids",
        bool(stage1.get("doc_ids")),
        json.dumps(stage1.get("doc_ids")),
    )

    # --- the pre-hook raises on a simulated write into evals/ ----------------
    raised = False
    try:
        hooks.pre_tool_use(
            "salesforce_writeback",
            {"case_id": "X", "resolution_summary": "y", "log_path": "evals/train/tasks.jsonl"},
        )
    except ProtectedPathError:
        raised = True
    check("pre-hook raises on a tool call carrying an evals/ path", raised)

    raised_dispatch = False
    try:
        dispatch("get_figure", {"doc_id": "d", "figure_id": str(ROOT / "evals" / "x.png")})
    except ProtectedPathError:
        raised_dispatch = True
    check("dispatcher enforces the pre-hook (does not swallow it)", raised_dispatch)

    raised_guard = False
    try:
        guard_write_path(ROOT / "observability" / "manifest" / "anything.json")
    except ProtectedPathError:
        raised_guard = True
    check("guard_write_path blocks observability/manifest/**", raised_guard)

    # --- the weak-retrieval trigger fires without needing a model -----------
    weak, reason = hooks.is_weak_retrieval(
        {"results": [{"doc_id": "a", "score": 0.11, "safety": False}]}
    )
    check("weak-retrieval condition detects a low top score", weak, str(reason))
    strong, _ = hooks.is_weak_retrieval(
        {"results": [{"doc_id": "a", "score": 9.9, "safety": False}]}
    )
    check("weak-retrieval condition leaves a strong hit alone", not strong)
    spans, reason_spans = hooks.is_weak_retrieval(
        {"results": [{"doc_id": "a", "score": 9.9}, {"doc_id": "b", "score": 9.8}]}
    )
    check("weak-retrieval condition detects results spanning docs", spans, str(reason_spans))

    print()
    if failures:
        print(f"{len(failures)} smoke test(s) failed: {', '.join(failures)}")
        return 1
    print("All smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
