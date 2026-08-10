"""The agent core. Hand-rolled tool loop — automatic function calling is OFF.

This is what the mobile app would use too. `turn()` takes text and returns text; the voice
layer is strictly above it. Nothing in this module knows that audio exists.
"""

from __future__ import annotations

import uuid
from typing import Any

from google.genai import types

from src.agent.dispatcher import dispatch
from src.agent.model_client import generate
from src.agent.prompts import GREETING, SYSTEM_PROMPT
from src.agent.skills import load_skills, render_skills
from src.agent.tool_schemas import orchestrator_tools
from src.config import MAX_TOOL_ROUNDS, orchestrator_model
from src.telemetry import span


class Orchestrator:
    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.skills = load_skills()
        self.system_prompt = SYSTEM_PROMPT + "\n\n" + render_skills(self.skills)
        self.contents: list[types.Content] = []
        # Sniffed from tool traffic so the evaluator subagent gets appliance context.
        self.model_number: str | None = None
        self.case_id: str | None = None
        self.config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            tools=orchestrator_tools(),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )

    # -- session context -----------------------------------------------------
    def greeting(self) -> str:
        return GREETING

    def _observe(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        """Track appliance context from tool traffic (not from model prose)."""
        if tool_name == "service_matters_search":
            self.model_number = args.get("model_number") or self.model_number
        if tool_name == "salesforce_lookup" and isinstance(result, dict):
            case = result.get("open_case") or {}
            if case:
                self.model_number = case.get("model_number") or self.model_number
                self.case_id = case.get("case_id") or self.case_id

    # -- the loop ------------------------------------------------------------
    def turn(self, user_text: str) -> str:
        """One technician utterance in, one spoken reply out."""
        with span("agent.turn", session_id=self.session_id) as turn_attrs:
            self.contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=user_text)])
            )

            rounds = 0
            for rounds in range(1, MAX_TOOL_ROUNDS + 1):
                response = generate(
                    model=orchestrator_model(),
                    contents=self.contents,
                    config=self.config,
                    span_name="agent.model_call",
                )
                calls = response.function_calls or []
                if not calls:
                    turn_attrs["tool_rounds"] = rounds - 1
                    reply = (response.text or "").strip()
                    if reply:
                        self.contents.append(response.candidates[0].content)
                    else:
                        reply = (
                            "Sorry, I lost that. Can you say it again?"
                        )
                    turn_attrs["reply_chars"] = len(reply)
                    return reply

                self.contents.append(response.candidates[0].content)
                parts: list[types.Part] = []
                for call in calls:
                    args = dict(call.args or {})
                    result = dispatch(call.name, args, model_number=self.model_number)
                    self._observe(call.name, args, result)
                    parts.append(
                        types.Part.from_function_response(
                            name=call.name, response={"result": result}
                        )
                    )
                self.contents.append(types.Content(role="user", parts=parts))

            # Ran out of tool rounds. Do not let the model spin on a live call.
            turn_attrs["tool_rounds"] = rounds
            turn_attrs["outcome"] = "tool_round_limit"
            self.contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text=(
                                "You have used your tool budget for this turn. Answer now "
                                "from what you already retrieved, or say you need to "
                                "transfer. Do not call any more tools."
                            )
                        )
                    ],
                )
            )
            final = generate(
                model=orchestrator_model(),
                contents=self.contents,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_prompt, temperature=0.2
                ),
                span_name="agent.model_call.forced_finish",
            )
            reply = (final.text or "").strip() or (
                "I'm having trouble finding that in the manual. Let me transfer you to a "
                "senior technician."
            )
            self.contents.append(
                types.Content(role="model", parts=[types.Part.from_text(text=reply)])
            )
            return reply
