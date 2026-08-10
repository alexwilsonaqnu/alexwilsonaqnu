"""Subagent: LLM-as-judge on retrieval quality.

A SEPARATE Gemini call on GEMINI_EVAL_MODEL with its own context. It may issue up to
MAX_EVALUATOR_TOOL_ROUNDS refinement `manual_search` calls; that exploration stays here
and never enters the orchestrator's context. Only the verdict crosses back.

The judge is high-volume and low-stakes, which is why it runs on the cheapest Garden model
that reliably emits the schema.
"""

from __future__ import annotations

import json
from typing import Any

from google.genai import types

from src.agent import hooks
from src.agent.model_client import generate
from src.agent.tool_schemas import VERDICT_JSON_SCHEMA, evaluator_tools
from src.config import AGENTS_DIR, MAX_EVALUATOR_TOOL_ROUNDS, eval_model
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
        # strip frontmatter
        if raw.startswith("---"):
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
        with span("subagent.retrieval_evaluator", model=eval_model()) as attrs:
            contents: list[types.Content] = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text=(
                                f"Technician question: {question}\n"
                                f"Appliance model: {model_number or 'unknown'}\n\n"
                                f"manual_search results:\n{_summarize(search_result)}"
                            )
                        )
                    ],
                )
            ]

            explore_config = types.GenerateContentConfig(
                system_instruction=_system_prompt(),
                tools=evaluator_tools(),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                temperature=0.0,
            )

            refinements = 0
            for _ in range(MAX_EVALUATOR_TOOL_ROUNDS):
                response = generate(
                    model=eval_model(),
                    contents=contents,
                    config=explore_config,
                    span_name="subagent.retrieval_evaluator.explore",
                )
                calls = response.function_calls or []
                if not calls:
                    break
                contents.append(response.candidates[0].content)
                parts = []
                for call in calls:
                    # Only manual_search is declared to the judge; anything else is refused.
                    if call.name != "manual_search":
                        payload: Any = {"error": "The evaluator may only call manual_search."}
                    else:
                        payload = dispatch(call.name, dict(call.args or {}), model_number=model_number)
                        refinements += 1
                    parts.append(types.Part.from_function_response(name=call.name, response={"result": payload}))
                contents.append(types.Content(role="user", parts=parts))

            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Now emit your verdict as a single JSON object.")],
                )
            )
            final = generate(
                model=eval_model(),
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_system_prompt(),
                    response_mime_type="application/json",
                    response_json_schema=VERDICT_JSON_SCHEMA,
                    temperature=0.0,
                ),
                span_name="subagent.retrieval_evaluator.verdict",
            )
            verdict = _parse_verdict(final.text)
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
