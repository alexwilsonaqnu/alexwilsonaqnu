"""Tool implementations and the name -> callable registry.

Nothing here calls a model, and nothing here is invoked directly by the orchestrator —
every call goes through src/agent/dispatcher.py so the hooks always run.
"""

from __future__ import annotations

from typing import Any, Callable

from src.tools.figures import get_figure
from src.tools.manual_search import manual_search
from src.tools.salesforce import salesforce_lookup, salesforce_writeback
from src.tools.service_matters import service_matters_search

TOOLS: dict[str, Callable[..., Any]] = {
    "salesforce_lookup": salesforce_lookup,
    "service_matters_search": service_matters_search,
    "manual_search": manual_search,
    "get_figure": get_figure,
    "salesforce_writeback": salesforce_writeback,
}

__all__ = [
    "TOOLS",
    "salesforce_lookup",
    "service_matters_search",
    "manual_search",
    "get_figure",
    "salesforce_writeback",
]
