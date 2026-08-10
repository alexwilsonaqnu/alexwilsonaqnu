"""Salesforce tools. Fixture-backed for the POC.

Seam: production swaps the fixture read for a Salesforce REST query and the JSONL append
for a Case/Task update. The tool contract does not change.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.config import SALESFORCE_FIXTURE_PATH, WRITEBACK_LOG_PATH
from src.guards import guard_write_path


def _load_fixture() -> dict[str, Any]:
    with SALESFORCE_FIXTURE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def salesforce_lookup(technician_id: str) -> dict[str, Any]:
    """Resolve a technician id to their identity and open case (if any)."""
    tech_id = (technician_id or "").strip().upper()
    for record in _load_fixture().get("technicians", []):
        if record["technician_id"].upper() == tech_id:
            return {
                "found": True,
                "technician_id": record["technician_id"],
                "technician_name": record["name"],
                "region": record.get("region"),
                "open_case": record.get("open_case"),
            }
    # Unknown techs return cleanly with no case so the agent falls back to disambiguation.
    return {
        "found": False,
        "technician_id": technician_id,
        "technician_name": None,
        "region": None,
        "open_case": None,
        "note": "No technician record matched. Ask for the model number directly.",
    }


def salesforce_writeback(case_id: str, resolution_summary: str) -> dict[str, Any]:
    """Append a resolution note for the case."""
    path = guard_write_path(WRITEBACK_LOG_PATH, actor="salesforce_writeback")
    record = {
        "case_id": case_id,
        "resolution_summary": resolution_summary,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "channel": "voice_agent_poc",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return {"written": True, "case_id": case_id, "log_path": str(path)}
