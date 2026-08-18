"""Deterministic retrieval grader. No model calls — this runs keyless.

    python scripts/run_evals.py evals/train/tasks.jsonl
    python scripts/run_evals.py evals/holdout/tasks.jsonl

Exit code is nonzero on any failure.

Per task it asserts:
  1. the expected doc id appears in the top-k results
  2. at least one expected signal string appears in the top-k text
  3. for safety tasks, at least one top-k chunk is safety-flagged
  4. the rank-1 result itself carries an expected signal string

(4) is what keeps this a ranking test rather than a recall test: on the POC's single
three-page corpus, top-k alone is nearly the whole index.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tools.manual_search import manual_search  # noqa: E402

DEFAULT_K = 3


def load_tasks(path: Path) -> list[dict]:
    tasks = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def grade(task: dict) -> tuple[bool, list[str]]:
    k = int(task.get("k", DEFAULT_K))
    # The task declares the model the technician is on; pass it. A warmed corpus carries
    # documents for the whole serviced fleet, and the agent never searches it unscoped —
    # stage 1 always narrows to the confirmed model first. Grading unscoped tested a
    # scenario the product does not have.
    hits = manual_search(
        task["question"], doc_ids=None, k=k, models=[task["model"]]
    ).get("results", [])
    failures: list[str] = []

    if not hits:
        return False, ["no results returned (has a manual been ingested?)"]

    expected_doc = task["expect_doc_contains"].lower()
    if not any(expected_doc in (h.get("doc_id") or "").lower() for h in hits):
        failures.append(
            f"expected doc containing {expected_doc!r}; got {[h.get('doc_id') for h in hits]}"
        )

    signals = task.get("expect_text_any", [])
    corpus = " \n ".join((h.get("text") or "") for h in hits).lower()
    if signals and not any(s.lower() in corpus for s in signals):
        failures.append(f"none of {signals} found in top-{k} text")

    if task.get("safety") and not any(h.get("safety") for h in hits):
        failures.append(f"safety task surfaced no safety-flagged chunk in top-{k}")

    top_text = (hits[0].get("text") or "").lower()
    if signals and not any(s.lower() in top_text for s in signals):
        failures.append(
            f"rank-1 hit (p{hits[0].get('page')}) carried none of {signals}"
        )

    return not failures, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grade retrieval against a task file.")
    parser.add_argument("tasks", type=Path)
    args = parser.parse_args(argv)

    if not args.tasks.exists():
        print(f"No such task file: {args.tasks}", file=sys.stderr)
        return 2

    tasks = load_tasks(args.tasks)
    passed = 0
    print(f"Grading {len(tasks)} task(s) from {args.tasks}\n")
    for task in tasks:
        ok, failures = grade(task)
        if ok:
            passed += 1
            print(f"  PASS  {task['id']}")
        else:
            print(f"  FAIL  {task['id']}")
            for failure in failures:
                print(f"          - {failure}")

    print(f"\n{passed}/{len(tasks)} passed")
    return 0 if passed == len(tasks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
