"""The brain seam.

Everything above this interface — orchestrator, evaluator, dispatcher, hooks, skills,
tools — is provider-agnostic. Swapping Claude for Gemini (or back) replaces one file
under src/agent/llm/ and nothing else.

Conversation history is provider-shaped and opaque to callers: you only ever touch it
through the client's append_* methods. That avoids a lossy neutral message format, which
is where brain-swap abstractions usually leak (thinking blocks, tool_use ids, signatures).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class LLMConfigError(RuntimeError):
    """Raised when the environment is not a valid configuration for this provider."""


@dataclass
class ToolSpec:
    """Provider-neutral tool declaration. Each client adapts it to its own wire format."""

    name: str
    description: str
    parameters: dict[str, Any]  # plain JSON Schema


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    call: ToolCall
    payload: Any


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    refused: bool = False
    raw: Any = None


class LLMClient(ABC):
    """One user turn in, one assistant turn out — plus history bookkeeping."""

    provider: str = "base"

    def __init__(self, model: str) -> None:
        self.model = model

    # -- history (opaque to callers) -----------------------------------------
    def new_history(self) -> list[Any]:
        return []

    @abstractmethod
    def append_user_text(self, history: list[Any], text: str) -> None: ...

    @abstractmethod
    def append_assistant(self, history: list[Any], turn: AssistantTurn) -> None: ...

    @abstractmethod
    def append_tool_results(self, history: list[Any], results: list[ToolResult]) -> None: ...

    # -- generation ----------------------------------------------------------
    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        history: list[Any],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
        span_name: str = "model.complete",
    ) -> AssistantTurn:
        """One model call. Tool dispatch is the caller's job — never the client's."""

    @abstractmethod
    def complete_json(
        self,
        *,
        system: str,
        history: list[Any],
        schema: dict[str, Any],
        max_tokens: int = 2048,
        span_name: str = "model.complete_json",
    ) -> str | None:
        """Constrained JSON generation. Returns raw JSON text for the caller to parse."""

    # -- preflight -----------------------------------------------------------
    @abstractmethod
    def preflight(self) -> tuple[bool, str]:
        """(ok, detail) — cheapest call that proves this model is reachable on this key."""

    @abstractmethod
    def remediation(self) -> str:
        """What the operator should do when preflight fails."""
