"""Brain selection. One env var picks the provider; nothing above this layer changes."""

from __future__ import annotations

from src.agent.llm.base import (
    AssistantTurn,
    LLMClient,
    LLMConfigError,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from src.config import (
    anthropic_eval_model,
    anthropic_fallbacks,
    anthropic_model,
    effort,
    eval_model,
    llm_provider,
    orchestrator_model,
)

_cache: dict[str, LLMClient] = {}


def get_llm(role: str = "orchestrator") -> LLMClient:
    """role is 'orchestrator' or 'retrieval_evaluator'."""
    provider = llm_provider()
    key = f"{provider}:{role}"
    if key in _cache:
        return _cache[key]

    if provider == "anthropic":
        from src.agent.llm.anthropic_client import AnthropicClient

        model = anthropic_model() if role == "orchestrator" else anthropic_eval_model()
        # effort, not temperature — sampling params are rejected on current Claude models.
        # "low" keeps thinking on (cheaper and safer than disabling it) while staying
        # inside a phone call's latency budget.
        client: LLMClient = AnthropicClient(
            model, effort=effort(), fallbacks=anthropic_fallbacks()
        )
    elif provider == "gemini":
        from src.agent.llm.gemini_client import GeminiClient

        model = orchestrator_model() if role == "orchestrator" else eval_model()
        client = GeminiClient(model)
    else:
        raise LLMConfigError(
            f"Unknown LLM_PROVIDER '{provider}'. Use 'anthropic' (default) or 'gemini'."
        )

    _cache[key] = client
    return client


def configured_clients() -> dict[str, LLMClient]:
    """Every brain this run will call, keyed by role. Preflight walks this."""
    return {role: get_llm(role) for role in ("orchestrator", "retrieval_evaluator")}


__all__ = [
    "AssistantTurn",
    "LLMClient",
    "LLMConfigError",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "configured_clients",
    "get_llm",
]
