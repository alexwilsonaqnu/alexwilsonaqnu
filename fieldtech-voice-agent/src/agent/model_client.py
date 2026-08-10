"""The only place a Gemini client is constructed. Model Garden / Vertex mode only.

Brain-swap seam: Claude models are served from the same Model Garden. Moving the brain to
Claude replaces this module and `tool_schemas.py`; the tenant, auth, skills, agent prompts
and tool contracts are untouched.
"""

from __future__ import annotations

import os
from typing import Any

from src.config import gcp_location, gcp_project
from src.telemetry import span

_client = None

_AI_STUDIO_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_VERTEX_FLAGS = ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE")


class ModelGardenConfigError(RuntimeError):
    """Raised when the environment is not a valid Model Garden configuration."""


def _flag_enabled() -> bool:
    # The SDK reads both names (GOOGLE_GENAI_USE_ENTERPRISE is the post-rename spelling and
    # wins on conflict); we accept either but require one to be explicitly true.
    return any(os.environ.get(flag, "").strip().lower() in ("true", "1") for flag in _VERTEX_FLAGS)


def assert_model_garden_env() -> None:
    """Fail loudly and early rather than letting an AI Studio key silently succeed."""
    present_keys = [key for key in _AI_STUDIO_KEYS if os.environ.get(key)]
    if present_keys:
        raise ModelGardenConfigError(
            f"{', '.join(present_keys)} is set. This POC talks to Model Garden only — the "
            "production tenant serves models exclusively through it, so an AI Studio key "
            "would exercise the wrong path (different project enablement, quotas and "
            f"regional availability). Unset {' and '.join(present_keys)} and use "
            "Application Default Credentials."
        )
    if not _flag_enabled():
        raise ModelGardenConfigError(
            "Set GOOGLE_GENAI_USE_VERTEXAI=true (the SDK also accepts the newer "
            "GOOGLE_GENAI_USE_ENTERPRISE=true) to initialize in Model Garden mode."
        )
    if not gcp_project():
        raise ModelGardenConfigError(
            "GOOGLE_CLOUD_PROJECT is not set. Model Garden calls are project-scoped."
        )


def get_client():
    """Return a memoized google-genai client bound to the Model Garden tenant."""
    global _client
    if _client is not None:
        return _client
    assert_model_garden_env()
    from google import genai

    # vertexai=True is passed explicitly as well as via env so the mode can never be
    # inferred from a stray credential.
    _client = genai.Client(vertexai=True, project=gcp_project(), location=gcp_location())
    return _client


def generate(*, model: str, contents: Any, config: Any, span_name: str = "model.generate") -> Any:
    """Single choke point for generate_content, so every model call is traced."""
    client = get_client()
    with span(span_name, model=model, project=gcp_project(), location=gcp_location()) as attrs:
        response = client.models.generate_content(model=model, contents=contents, config=config)
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            attrs["prompt_tokens"] = getattr(usage, "prompt_token_count", None)
            attrs["candidates_tokens"] = getattr(usage, "candidates_token_count", None)
        attrs["function_calls"] = len(response.function_calls or [])
        return response
