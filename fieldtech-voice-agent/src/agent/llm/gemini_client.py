"""Gemini brain — Model Garden on the Gemini Enterprise Agent Platform (Vertex) only.

Kept as the alternate provider so the brain-swap claim is demonstrated rather than
asserted: same skills, same agent prompts, same tool contracts, same hooks.

Verified against google-genai 2.17.0:
  - function declarations use parameters_json_schema and are passed through verbatim to
    the Vertex payload as functionDeclarations.
  - automatic function calling must be explicitly disabled so our dispatcher owns the loop.
  - the SDK reads both GOOGLE_GENAI_USE_VERTEXAI and the newer GOOGLE_GENAI_USE_ENTERPRISE
    (enterprise wins on conflict).
"""

from __future__ import annotations

import os
from typing import Any

from src.agent.llm.base import (
    AssistantTurn,
    LLMClient,
    LLMConfigError,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from src.telemetry import span

_AI_STUDIO_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_VERTEX_FLAGS = ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE")


class GeminiClient(LLMClient):
    provider = "gemini"

    def __init__(self, model: str, *, temperature: float = 0.2) -> None:
        super().__init__(model)
        self.temperature = temperature
        self._client = None

    # -- client --------------------------------------------------------------
    def _assert_env(self) -> None:
        present = [k for k in _AI_STUDIO_KEYS if os.environ.get(k)]
        if present:
            raise LLMConfigError(
                f"{', '.join(present)} is set. The Gemini brain talks to Model Garden only — "
                "the production tenant serves models exclusively through it, so an AI Studio "
                "key would exercise the wrong path (different project enablement, quotas and "
                f"regional availability). Unset {' and '.join(present)} and use ADC."
            )
        if not any(os.environ.get(f, "").strip().lower() in ("true", "1") for f in _VERTEX_FLAGS):
            raise LLMConfigError(
                "Set GOOGLE_GENAI_USE_VERTEXAI=true (the SDK also accepts the newer "
                "GOOGLE_GENAI_USE_ENTERPRISE=true) to initialize in Model Garden mode."
            )
        if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
            raise LLMConfigError(
                "GOOGLE_CLOUD_PROJECT is not set. Model Garden calls are project-scoped."
            )

    def _api(self):
        if self._client is not None:
            return self._client
        self._assert_env()
        from google import genai

        # vertexai=True passed explicitly as well as via env, so the mode can never be
        # inferred from a stray credential.
        self._client = genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
        )
        return self._client

    # -- history -------------------------------------------------------------
    def append_user_text(self, history: list[Any], text: str) -> None:
        from google.genai import types

        history.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))

    def append_assistant(self, history: list[Any], turn: AssistantTurn) -> None:
        from google.genai import types

        if turn.raw is not None:
            history.append(turn.raw)
        else:
            history.append(
                types.Content(role="model", parts=[types.Part.from_text(text=turn.text)])
            )

    def append_tool_results(self, history: list[Any], results: list[ToolResult]) -> None:
        from google.genai import types

        parts = [
            types.Part.from_function_response(
                name=result.call.name, response={"result": result.payload}
            )
            for result in results
        ]
        history.append(types.Content(role="user", parts=parts))

    # -- generation ----------------------------------------------------------
    def _tools(self, tools: list[ToolSpec] | None):
        from google.genai import types

        if not tools:
            return None
        declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.parameters
            )
            for t in tools
        ]
        return [types.Tool(function_declarations=declarations)]

    def complete(
        self,
        *,
        system: str,
        history: list[Any],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
        span_name: str = "model.complete",
    ) -> AssistantTurn:
        from google.genai import types

        with span(span_name, provider=self.provider, model=self.model) as attrs:
            config = types.GenerateContentConfig(
                system_instruction=system,
                temperature=self.temperature,
                max_output_tokens=max_tokens,
                tools=self._tools(tools),
                # OFF on purpose: our dispatcher owns the loop, which is what makes the
                # hooks enforceable in code rather than in prompt text.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
            response = self._api().models.generate_content(
                model=self.model, contents=history, config=config
            )
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                attrs["input_tokens"] = getattr(usage, "prompt_token_count", None)
                attrs["output_tokens"] = getattr(usage, "candidates_token_count", None)

            calls = [
                ToolCall(id=fc.id or f"{fc.name}-{i}", name=fc.name, args=dict(fc.args or {}))
                for i, fc in enumerate(response.function_calls or [])
            ]
            attrs["tool_calls"] = len(calls)
            raw = response.candidates[0].content if response.candidates else None
            return AssistantTurn(
                text=(response.text or "").strip() if not calls else "",
                tool_calls=calls,
                stop_reason="tool_use" if calls else "end_turn",
                raw=raw,
            )

    def complete_json(
        self,
        *,
        system: str,
        history: list[Any],
        schema: dict[str, Any],
        max_tokens: int = 2048,
        span_name: str = "model.complete_json",
    ) -> str | None:
        from google.genai import types

        with span(span_name, provider=self.provider, model=self.model):
            response = self._api().models.generate_content(
                model=self.model,
                contents=history,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.0,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                ),
            )
            return response.text

    # -- preflight -----------------------------------------------------------
    def preflight(self) -> tuple[bool, str]:
        try:
            response = self._api().models.count_tokens(model=self.model, contents="ping")
            return True, f"count_tokens ok (total_tokens={getattr(response, 'total_tokens', '?')})"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def remediation(self) -> str:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        return (
            f"    -> Enable '{self.model}' in Model Garden for project {project} in region "
            f"{location}, confirm the model id is still current (Garden ids churn), and "
            f"check that your ADC principal has roles/aiplatform.user."
        )
