#!/usr/bin/env python3
"""Stop → provenance-stop-gate (§7). Matcher: *.

The backstop that makes "the LLM never invents a number" a PROPERTY of the
system rather than a request. On every agent stop, scan the drafted output for
numeric tokens (currencies, percentages, multiples, counts). For each, require a
matching ledger fact (value-equality within display tolerance). Any unsourced
number → block (exit 2) with the offending number and instruction to fetch it
via the retriever or remove it.

The harness then re-runs the generator with this feedback. The provenance-
evaluator subagent owns the richer rubric (faithfulness/completeness/craft);
THIS hook owns the two HARD gates: every number sourced (here) and no LLM math
(no-math-gate, PreToolUse). Hard gates are non-negotiable — a failing build, not
a quality nit (§13).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fpna_common import (  # noqa: E402
    extract_output_numbers,
    fact_number,
    find_session_id,
    load_facts,
    numbers_equal,
    read_hook_input,
)


def last_assistant_text(transcript_path: str | None) -> str:
    """Pull the text of the final assistant message from a Claude Code transcript."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    text_parts: list[str] = []
    with open(transcript_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            msg = entry.get("message", {})
            content = msg.get("content", [])
            parts: list[str] = []
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
            if parts:
                text_parts = parts  # keep only the latest assistant turn
    return "\n".join(text_parts)


def token_is_sourced(magnitude: float, facts: list[dict]) -> bool:
    for f in facts:
        fv = fact_number(f)
        if fv != fv:  # NaN
            continue
        # match the figure directly, or as a percent<->fraction pair
        if (
            numbers_equal(magnitude, fv)
            or numbers_equal(magnitude, fv * 100.0)
            or numbers_equal(magnitude, fv / 100.0)
        ):
            return True
    return False


def runtime_active() -> bool:
    """Enforce only inside the FP&A runtime.

    The Stop hook has matcher `*`, so it fires on EVERY agent stop. We only want
    to vet FP&A *deliverable* outputs — not an ordinary interactive/dev session
    at the repo root that happens to mention a number ("33 tests pass", "§2B").

    The orchestrator, crons, and the SDK runtime set FPNA_RUNTIME=1 (and a
    session id); a plain `claude` session does not. CRITICAL: this does NOT
    weaken the backstop where it matters — inside the runtime, an FP&A answer
    with an empty ledger and stated numbers (the silently-failed-retrieval case)
    still blocks. We pass through only OUTSIDE the runtime, where there is no
    deliverable to protect.
    """
    if os.environ.get("FPNA_RUNTIME", "").lower() in ("1", "true", "yes"):
        return True
    return bool(os.environ.get("FPNA_SESSION_ID"))


def main() -> None:
    payload = read_hook_input()

    if not runtime_active():
        sys.exit(0)  # not an FP&A deliverable path; nothing to enforce

    # draft text: prefer explicit payload/env (tests, headless), else transcript
    draft = (
        payload.get("draft")
        or os.environ.get("FPNA_DRAFT")
        or last_assistant_text(payload.get("transcript_path"))
    )
    if not draft.strip():
        sys.exit(0)  # nothing to vet

    session_id = find_session_id(payload)
    cwd = payload.get("cwd")
    facts = load_facts(session_id, cwd) if session_id else []

    unsourced: list[str] = []
    seen: set[str] = set()
    for raw, magnitude in extract_output_numbers(draft):
        key = f"{magnitude:.6g}"
        if key in seen:
            continue
        seen.add(key)
        if not token_is_sourced(magnitude, facts):
            unsourced.append(raw.strip())

    if unsourced:
        listed = ", ".join(unsourced[:15])
        more = "" if len(unsourced) <= 15 else f" (+{len(unsourced) - 15} more)"
        msg = (
            "[provenance-stop-gate] BLOCKED: the draft states numbers that are not "
            f"in the provenance ledger:\n    {listed}{more}\n\n"
            "Every figure that leaves this system must trace to an AnaplanFact "
            "(§0/§3). For each number above either:\n"
            "  • have anaplan-retriever fetch it (read a line item, or one Calcite "
            "query that returns it AS a derived alias), so it lands in the ledger; or\n"
            "  • remove it / render it as a {{fact:<requestId>}} reference the "
            "assembler resolves.\n"
            f"Ledger currently holds {len(facts)} fact(s) for this session."
        )
        print(msg, file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
