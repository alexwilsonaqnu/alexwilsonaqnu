"""Subagent: LLM-as-judge on retrieval quality.

A SEPARATE model call with its own context and its own (cheaper) model. It may issue up
to MAX_EVALUATOR_TOOL_ROUNDS refinement `manual_search` calls; that exploration stays here
and never enters the orchestrator's context. Only the verdict crosses back.
"""

from __future__ import annotations

import json
from typing import Any

from src.agent import hooks
from src.agent.llm import ToolResult, get_llm
from src.agent.tool_schemas import EVALUATOR_TOOLS, VERDICT_JSON_SCHEMA
from src.config import AGENTS_DIR, MAX_EVALUATOR_TOKENS, MAX_EVALUATOR_TOOL_ROUNDS
from src.telemetry import span

_prompt_cache: str | None = None

_FALLBACK_VERDICT = {
    "verdict": "ESCALATE",
    "reasoning": "Retrieval evaluator unavailable; escalating rather than guessing.",
    "suggested_query": "",
}


def _system_prompt() -> str:
    global _prompt_cache
    if _prompt_cache is None:
        raw = (AGENTS_DIR / "retrieval-evaluator.md").read_text(encoding="utf-8")
        if raw.startswith("---"):  # strip frontmatter
            parts = raw.split("---", 2)
            raw = parts[2] if len(parts) >= 3 else raw
        _prompt_cache = raw.strip()
    return _prompt_cache


def _summarize(search_result: dict[str, Any]) -> str:
    lines = []
    for hit in search_result.get("results", []):
        lines.append(
            f"- doc_id={hit.get('doc_id')} page={hit.get('page')} score={hit.get('score')} "
            f"safety={hit.get('safety')}\n  text: {(hit.get('text') or '')[:900]}"
        )
    return "\n".join(lines) or "(no results)"


def _parse_verdict(text: str | None) -> dict[str, Any]:
    if not text:
        return dict(_FALLBACK_VERDICT)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return dict(_FALLBACK_VERDICT)
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return dict(_FALLBACK_VERDICT)
    if not isinstance(parsed, dict) or parsed.get("verdict") not in {
        "ANSWERABLE",
        "NEEDS_RERETRIEVAL",
        "ESCALATE",
    }:
        return dict(_FALLBACK_VERDICT)
    return {
        "verdict": parsed["verdict"],
        "reasoning": str(parsed.get("reasoning", ""))[:500],
        "suggested_query": str(parsed.get("suggested_query", "")),
    }


def evaluate_retrieval(
    *,
    question: str,
    model_number: str | None,
    search_result: dict[str, Any],
) -> dict[str, Any]:
    """Judge a manual_search result. Returns the verdict dict — never raises."""
    from src.agent.dispatcher import dispatch  # local import: dispatcher imports hooks

    token = hooks.evaluator_context().set(True)
    try:
        llm = get_llm("retrieval_evaluator")
        with span(
            "subagent.retrieval_evaluator", provider=llm.provider, model=llm.model
        ) as attrs:
            history = llm.new_history()
            llm.append_user_text(
                history,
                f"Technician question: {question}\n"
                f"Appliance model: {model_number or 'unknown'}\n\n"
                f"manual_search results:\n{_summarize(search_result)}",
            )

            refinements = 0
            for _ in range(MAX_EVALUATOR_TOOL_ROUNDS):
                assistant = llm.complete(
                    system=_system_prompt(),
                    history=history,
                    tools=EVALUATOR_TOOLS,
                    max_tokens=MAX_EVALUATOR_TOKENS,
                    span_name="subagent.retrieval_evaluator.explore",
                )
                if assistant.refused or not assistant.tool_calls:
                    break
                llm.append_assistant(history, assistant)
                results: list[ToolResult] = []
                for call in assistant.tool_calls:
                    # Only manual_search is declared to the judge; refuse anything else.
                    if call.name != "manual_search":
                        payload: Any = {"error": "The evaluator may only call manual_search."}
                    else:
                        payload = dispatch(call.name, call.args, model_number=model_number)
                        refinements += 1
                    results.append(ToolResult(call=call, payload=payload))
                llm.append_tool_results(history, results)

            llm.append_user_text(history, "Now emit your verdict as a single JSON object.")
            verdict = _parse_verdict(
                llm.complete_json(
                    system=_system_prompt(),
                    history=history,
                    schema=VERDICT_JSON_SCHEMA,
                    max_tokens=MAX_EVALUATOR_TOKENS,
                    span_name="subagent.retrieval_evaluator.verdict",
                )
            )
            attrs["verdict"] = verdict["verdict"]
            attrs["refinement_searches"] = refinements
            return verdict
    except Exception as exc:
        # A judge failure must never take down the call. Fail toward escalation.
        with span("subagent.retrieval_evaluator.failed") as attrs:
            attrs["error"] = f"{type(exc).__name__}: {exc}"
        return dict(_FALLBACK_VERDICT)
    finally:
        hooks.evaluator_context().reset(token)
