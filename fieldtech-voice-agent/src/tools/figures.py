"""Figure delivery. Diagrams cannot be spoken, so the agent pushes them to the app.

Seam: production replaces the JSONL append with a real push to the technician's mobile
app (and the returned path with a signed URL).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.config import FIGURE_PUSHES_PATH, FIGURES_DIR
from src.guards import guard_write_path


def get_figure(doc_id: str, figure_id: str) -> dict[str, Any]:
    """Resolve an extracted figure to a file and log its delivery."""
    candidate = FIGURES_DIR / f"{figure_id}.png"
    if not candidate.exists():
        # figure ids are recorded on chunks as "<doc_id>_pN_figM"; accept a bare "pN_figM"
        candidate = FIGURES_DIR / f"{doc_id}_{figure_id}.png"
    if not candidate.exists():
        return {
            "delivered": False,
            "doc_id": doc_id,
            "figure_id": figure_id,
            "error": "Figure not found among extracted images.",
        }

    path = guard_write_path(FIGURE_PUSHES_PATH, actor="get_figure")
    record = {
        "doc_id": doc_id,
        "figure_id": figure_id,
        "figure_path": str(candidate),
        "delivery_channel": "app_push",
        "delivered_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    return {
        "delivered": True,
        "doc_id": doc_id,
        "figure_id": figure_id,
        "figure_path": str(candidate),
        "delivery_channel": "app_push",
        "say": "I am pushing that diagram to your app now.",
    }
