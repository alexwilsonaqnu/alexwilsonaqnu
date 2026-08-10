"""Claude brain — the Anthropic API directly (API key, no cloud project).

Verified against the anthropic Python SDK 0.121.0:
  - tools: {"name", "description", "input_schema"} — plain JSON Schema, no wrapper.
  - manual loop: stop_reason == "tool_use"; append response.content wholesale, then send
    ALL tool_result blocks back in ONE user message (splitting them across messages
    teaches the model to stop making parallel calls).
  - structured output: output_config={"format": {"type": "json_schema", "schema": ...}}.
    Assistant prefill is removed on current models and returns a 400 — do not reintroduce it.
  - no temperature / top_p / top_k: these are removed on Claude Opus 5 and return a 400.
    Steer with the prompt; control depth with output_config.effort instead.
  - thinking is ON by default on Opus 5 and max_tokens caps thinking + text together.
    We leave it on and use effort="low" for voice latency: disabling thinking is the more
    expensive lever and can make the model emit a tool call as plain text (the call then
    silently never runs) or leak <thinking> tags into the spoken reply.
"""

from __future__ import annotations

import json
import os
import sys
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

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(self, model: str, *, effort: str = "low", fallbacks: str | None = "default") -> None:
        super().__init__(model)
        self.effort = effort
        # A safety classifier can decline a request; a fallback re-runs it server-side on
        # a recommended model rather than dropping a technician's call. Set
        # ANTHROPIC_FALLBACKS="" to turn this off.
        self.fallbacks = fallbacks or None
        self._client = None

    # -- client --------------------------------------------------------------
    def _api(self):
        if self._client is not None:
            return self._client

        raw = os.environ.get("ANTHROPIC_API_KEY")
        if not raw or not raw.strip():
            raise LLMConfigError(
                "ANTHROPIC_API_KEY is not set. Export it (from console.anthropic.com) and "
                "re-run. No other credential is needed for the Claude brain."
            )
        if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            # The SDK would send both credential headers and the API rejects that with a
            # 401 that reads like a bad key.
            raise LLMConfigError(
                "Both ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN are set. The SDK sends "
                "both and the API rejects the request. Unset one — for this POC, keep "
                "ANTHROPIC_API_KEY."
            )

        key = raw.strip()
        if key != raw:
            # A trailing newline or space survives a copy-paste and produces a 401 that
            # looks exactly like an invalid key. Pass the key explicitly, stripped.
            print(
                "  note: stripped surrounding whitespace from ANTHROPIC_API_KEY",
                file=sys.stderr,
            )
        for bad in ('"', "'", "“", "”"):
            if bad in key:
                raise LLMConfigError(
                    "ANTHROPIC_API_KEY contains a quote character. Paste the key bare in "
                    ".env — no quotes, no `export`, nothing after the `=` but the key."
                )

        import anthropic

        self._client = anthropic.Anthropic(api_key=key)
        return self._client

    # -- history -------------------------------------------------------------
    def append_user_text(self, history: list[Any], text: str) -> None:
        history.append({"role": "user", "content": [{"type": "text", "text": text}]})

    def append_assistant(self, history: list[Any], turn: AssistantTurn) -> None:
        # Append the raw content wholesale — it carries tool_use blocks (and thinking
        # blocks) the API needs back verbatim on the next turn.
        content = turn.raw if turn.raw is not None else [{"type": "text", "text": turn.text}]
        history.append({"role": "assistant", "content": content})

    def append_tool_results(self, history: list[Any], results: list[ToolResult]) -> None:
        blocks = [
            {
                "type": "tool_result",
                "tool_use_id": result.call.id,
                "content": json.dumps(result.payload, default=str),
                **({"is_error": True} if _is_error(result.payload) else {}),
            }
            for result in results
        ]
        # All results in ONE user message — see module docstring.
        history.append({"role": "user", "content": blocks})

    # -- generation ----------------------------------------------------------
    def _tools(self, tools: list[ToolSpec] | None) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in (tools or [])
        ]

    def _create(self, *, use_tools: bool, **kwargs):
        client = self._api()
        if self.fallbacks and use_tools:
            return client.beta.messages.create(
                betas=[_FALLBACK_BETA], fallbacks=self.fallbacks, **kwargs
            )
        return client.messages.create(**kwargs)

    def complete(
        self,
        *,
        system: str,
        history: list[Any],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 4096,
        span_name: str = "model.complete",
    ) -> AssistantTurn:
        with span(span_name, provider=self.provider, model=self.model, effort=self.effort) as attrs:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": history,
                # effort, not temperature — sampling params are rejected on Opus 5.
                "output_config": {"effort": self.effort},
            }
            if tools:
                kwargs["tools"] = self._tools(tools)
            response = self._create(use_tools=bool(tools), **kwargs)

            usage = getattr(response, "usage", None)
            if usage is not None:
                attrs["input_tokens"] = getattr(usage, "input_tokens", None)
                attrs["output_tokens"] = getattr(usage, "output_tokens", None)
            attrs["stop_reason"] = response.stop_reason

            # Check stop_reason before reading content: on a refusal, content may be
            # empty or partial and indexing content[0] would blow up.
            if response.stop_reason == "refusal":
                attrs["refused"] = True
                return AssistantTurn(stop_reason="refusal", refused=True, raw=None)

            text_parts, calls = [], []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    calls.append(ToolCall(id=block.id, name=block.name, args=dict(block.input or {})))
            attrs["tool_calls"] = len(calls)
            return AssistantTurn(
                text="".join(text_parts).strip(),
                tool_calls=calls,
                stop_reason=response.stop_reason,
                raw=response.content,
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
        with span(span_name, provider=self.provider, model=self.model) as attrs:
            response = self._api().messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=history,
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
            attrs["stop_reason"] = response.stop_reason
            if response.stop_reason == "refusal":
                return None
            return "".join(b.text for b in response.content if b.type == "text") or None

    # -- preflight -----------------------------------------------------------
    def preflight(self) -> tuple[bool, str]:
        try:
            result = self._api().messages.count_tokens(
                model=self.model, messages=[{"role": "user", "content": "ping"}]
            )
            return True, f"count_tokens ok (input_tokens={result.input_tokens})"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def remediation(self) -> str:
        return (
            f"    -> Check ANTHROPIC_API_KEY is set and valid, that your organization has "
            f"access to '{self.model}', and that the model id is still current "
            f"(ids change between releases — this is an env change, not a code change)."
        )


def _is_error(payload: Any) -> bool:
    return isinstance(payload, dict) and "error" in payload
