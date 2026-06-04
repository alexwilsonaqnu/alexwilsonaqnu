#!/usr/bin/env python3
"""PostToolUse → ledger-append (§7).

Matcher: mcp__anaplan-.* (Anaplan reads/explains/exports/recomputes).

Parses the result of every Anaplan tool call, extracts each numeric value with
its intersection, and appends an AnaplanFact to `.fpna/ledger/<session>.jsonl`.

THIS IS THE ALLOWLIST OF NUMBERS THE SYSTEM MAY SPEAK. A figure may leave the
system only if it landed here first (the Stop-gate enforces that). The hook is
deliberately permissive about input shape (best-effort parse) and never blocks —
it can only fail open into "fewer facts", never into "wrong facts", because it
copies values verbatim from the engine response.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fpna_common import (  # noqa: E402
    append_fact,
    find_session_id,
    iter_numbers,
    read_hook_input,
)

# tool_name → fact source (§3)
SOURCE_BY_TOOL = {
    "aocfo_sql_query": "sql_calcite",
    "aocfo_explain_cell": "explain_cell",
    "create_view_readrequest": "line_item",
    "get_view_readrequest_page": "line_item",
    "run_export": "line_item",
}

NUMERIC_HINTS = ("delta", "variance", "var", "yoy", "growth", "pct", "%",
                 "change", "margin", "revenue", "ratio", "contribution")


def derive_source(tool_name: str, tool_input: dict[str, Any]) -> str:
    bare = tool_name.split("__")[-1]
    if bare in SOURCE_BY_TOOL:
        return SOURCE_BY_TOOL[bare]
    # writes that were read back land here too; assume recompute reads
    if "write" in bare or "import" in bare or "process" in bare:
        return "scenario_recompute"
    return "line_item"


def unwrap_response(tool_response: Any) -> Any:
    """MCP tool results often arrive as {content:[{type:'text',text:'<json>'}]}."""
    if isinstance(tool_response, dict) and "content" in tool_response:
        parts = tool_response.get("content") or []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                txt = part.get("text", "")
                try:
                    return json.loads(txt)
                except (json.JSONDecodeError, TypeError):
                    return txt
    return tool_response


def rows_and_columns(data: Any) -> tuple[list[str], list[list[Any]]]:
    """Best-effort extraction of a tabular result (rows of cells)."""
    if isinstance(data, dict):
        cols = data.get("columns") or data.get("schema") or []
        cols = [c.get("name") if isinstance(c, dict) else str(c) for c in cols]
        rows = data.get("rows") or data.get("data") or data.get("results") or []
        norm: list[list[Any]] = []
        for r in rows:
            if isinstance(r, dict):
                if not cols:
                    cols = list(r.keys())
                norm.append([r.get(c) for c in cols])
            elif isinstance(r, list):
                norm.append(r)
        return cols, norm
    if isinstance(data, list) and data and isinstance(data[0], dict):
        cols = list(data[0].keys())
        return cols, [[r.get(c) for c in cols] for r in data]
    return [], []


def is_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        s = v.replace(",", "").replace("$", "").replace("%", "").strip()
        try:
            float(s)
            return True
        except ValueError:
            return False
    return False


def main() -> None:
    payload = read_hook_input()
    session_id = find_session_id(payload)
    if not session_id:
        sys.exit(0)  # nothing to anchor the ledger to; fail open
    cwd = payload.get("cwd")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    data = unwrap_response(payload.get("tool_response"))

    source = derive_source(tool_name, tool_input)
    query = tool_input.get("query") if source == "sql_calcite" else None
    base = {
        "workspaceId": tool_input.get("workspaceId")
        or os.environ.get("ANAPLAN_WS_GUID", ""),
        "modelId": tool_input.get("modelId")
        or os.environ.get("ANAPLAN_MODEL_GUID", ""),
        "module": tool_input.get("module") or tool_input.get("table") or "",
        "version": tool_input.get("version"),
        "source": source,
    }
    if query:
        base["query"] = query

    count = 0
    cols, rows = rows_and_columns(data)
    if cols and rows:
        for r_idx, row in enumerate(rows):
            cells = dict(zip(cols, row))
            intersection = {
                c: str(v) for c, v in cells.items() if not is_number(v) and v is not None
            }
            for c, v in cells.items():
                if not is_number(v):
                    continue
                line_item = c
                fact = {
                    **base,
                    "value": float(str(v).replace(",", "").replace("$", "").replace("%", ""))
                    if isinstance(v, str)
                    else v,
                    "label": f"{base['module']} · {line_item}".strip(" ·")
                    or line_item,
                    "lineItem": line_item,
                    "intersection": intersection,
                    "requestId": f"req_{uuid.uuid4().hex[:8]}",
                    "fetchedAt": datetime.now(timezone.utc).isoformat(),
                }
                append_fact(session_id, fact, cwd)
                count += 1

    if count == 0:
        # fall back to a structural walk so explain_cell / odd shapes still log
        for path, num in iter_numbers(data):
            fact = {
                **base,
                "value": num,
                "label": ".".join(path) or (base["module"] or tool_name),
                "lineItem": path[-1] if path else tool_name,
                "intersection": {},
                "requestId": f"req_{uuid.uuid4().hex[:8]}",
                "fetchedAt": datetime.now(timezone.utc).isoformat(),
            }
            append_fact(session_id, fact, cwd)
            count += 1

    # stamp OTel span correlation on stdout (collector picks it up; harmless if not)
    print(json.dumps({"fpna_ledger_appended": count, "session": session_id}))
    sys.exit(0)


if __name__ == "__main__":
    main()
