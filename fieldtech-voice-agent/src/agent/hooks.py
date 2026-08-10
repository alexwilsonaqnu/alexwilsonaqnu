"""Hooks over prompts.

There is no `.claude/` runtime here, so the hook layer is this module, called
deterministically by the dispatcher around EVERY tool call. These are guarantees the model
cannot reason around: it does not get asked, it does not get to explain why this time is
different.

  pre_tool_use   — blocks any tool from writing under evals/** or observability/manifest/**
  post_tool_use  — auto-triggers the retrieval evaluator on weak manual_search results
"""

from __future__ import annotations

import contextvars
from typing import Any

from src.config import WEAK_RETRIEVAL_SCORE
from src.guards import ProtectedPathError, is_protected
from src.telemetry import span

# Set while the retrieval evaluator is running so its own manual_search calls do not
# recursively re-trigger the evaluator.
_in_evaluator: contextvars.ContextVar[bool] = contextvars.ContextVar("in_evaluator", default=False)


def evaluator_context() -> contextvars.ContextVar[bool]:
    return _in_evaluator


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)


def pre_tool_use(tool_name: str, args: dict[str, Any]) -> None:
    """Raise if this call would write into a read-only tree. Never asks the model."""
    for candidate in _walk_strings(args):
        if ("/" in candidate or "\\" in candidate) and is_protected(candidate):
            with span("hook.pre_tool_use.blocked", tool=tool_name, path=candidate):
                pass
            raise ProtectedPathError(
                f"Blocked {tool_name}: '{candidate}' is inside a read-only tree "
                "(evals/** and observability/manifest/** are not writable by tools)."
            )


def is_weak_retrieval(result: dict[str, Any]) -> tuple[bool, str | None]:
    """The post-hook trigger condition, factored out so it is testable without a model."""
    results = result.get("results") or []
    if not results:
        return True, "no_results"
    top_score = max(float(hit.get("score") or 0.0) for hit in results)
    if top_score < WEAK_RETRIEVAL_SCORE:
        return True, f"weak_top_score:{top_score:.3f}"
    doc_ids = {hit.get("doc_id") for hit in results}
    if len(doc_ids) > 1:
        return True, f"spans_docs:{len(doc_ids)}"
    return False, None


def post_tool_use(
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    *,
    model_number: str | None = None,
) -> Any:
    """On weak manual_search results, attach a retrieval verdict from the subagent."""
    if tool_name != "manual_search" or not isinstance(result, dict):
        return result
    if _in_evaluator.get():
        return result  # the judge's own refinement calls are not re-judged

    weak, reason = is_weak_retrieval(result)
    if not weak:
        return result

    with span("hook.post_tool_use.evaluator_triggered", tool=tool_name, reason=reason) as attrs:
        # Imported lazily: the evaluator imports the dispatcher, which imports this module.
        from src.agent.retrieval_evaluator import evaluate_retrieval

        verdict = evaluate_retrieval(
            question=str(args.get("query", "")),
            model_number=model_number,
            search_result=result,
        )
        attrs["verdict"] = verdict.get("verdict")

    result["retrieval_evaluation"] = {"trigger": reason, **verdict}
    return result
