"""The tool dispatcher. Every tool call in this system goes through here.

Automatic function calling is off in both the orchestrator and the evaluator precisely so
that this function exists: it is the single place where the hooks are guaranteed to run.
"""

from __future__ import annotations

from typing import Any

from src.agent import hooks
from src.guards import ProtectedPathError
from src.telemetry import span
from src.tools import TOOLS


def dispatch(tool_name: str, args: dict[str, Any], *, model_number: str | None = None) -> Any:
    """Run pre-hook, the tool, then post-hook. Returns a JSON-serializable result."""
    args = dict(args or {})
    with span("tool.call", tool=tool_name, args=args) as attrs:
        # Pre-hook first: a blocked call never reaches the tool.
        hooks.pre_tool_use(tool_name, args)

        tool = TOOLS.get(tool_name)
        if tool is None:
            attrs["outcome"] = "unknown_tool"
            return {"error": f"Unknown tool '{tool_name}'."}

        try:
            result = tool(**args)
        except ProtectedPathError:
            # The write boundary is not a recoverable condition. Let it kill the turn.
            raise
        except TypeError as exc:
            attrs["outcome"] = "bad_arguments"
            return {"error": f"Invalid arguments for {tool_name}: {exc}"}
        except Exception as exc:
            # A flaky fixture or network read must not end a technician's call — hand the
            # failure back to the model so it can recover or transfer.
            attrs["outcome"] = "tool_error"
            return {"error": f"{tool_name} failed: {type(exc).__name__}: {exc}"}

        result = hooks.post_tool_use(tool_name, args, result, model_number=model_number)
        attrs["outcome"] = "ok"
        if isinstance(result, dict) and isinstance(result.get("results"), list):
            attrs["result_count"] = len(result["results"])
        return result
