"""The agent core. Hand-rolled tool loop — automatic tool execution is OFF.

This is what the mobile app would use too. `turn()` takes text and returns text; the voice
layer is strictly above it. Nothing in this module knows that audio exists, and nothing in
it knows which brain is answering.
"""

from __future__ import annotations

import uuid
from typing import Any

from src.agent.dispatcher import dispatch
from src.agent.llm import ToolResult, get_llm
from src.agent.prompts import GREETING, SYSTEM_PROMPT
from src.agent.skills import load_skills, render_skills
from src.agent.tool_schemas import ORCHESTRATOR_TOOLS
from src.config import MAX_TOOL_ROUNDS, MAX_TURN_TOKENS
from src.telemetry import span

_TRANSFER = (
    "I'm not able to help with that one. Let me transfer you to a senior technician."
)
_RETRY = "Sorry, I lost that. Can you say it again?"


class Orchestrator:
    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.llm = get_llm("orchestrator")
        self.skills = load_skills()
        self.system_prompt = SYSTEM_PROMPT + "\n\n" + render_skills(self.skills)
        self.history = self.llm.new_history()
        # Sniffed from tool traffic so the evaluator subagent gets appliance context.
        self.model_number: str | None = None
        self.case_id: str | None = None

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
        with span(
            "agent.turn",
            session_id=self.session_id,
            provider=self.llm.provider,
            model=self.llm.model,
        ) as turn_attrs:
            self.llm.append_user_text(self.history, user_text)

            rounds = 0
            for rounds in range(1, MAX_TOOL_ROUNDS + 1):
                assistant = self.llm.complete(
                    system=self.system_prompt,
                    history=self.history,
                    tools=ORCHESTRATOR_TOOLS,
                    max_tokens=MAX_TURN_TOKENS,
                    span_name="agent.model_call",
                )

                # A safety classifier declined. Don't retry the same prompt at the
                # technician — hand off.
                if assistant.refused:
                    turn_attrs["outcome"] = "refused"
                    return _TRANSFER

                if not assistant.tool_calls:
                    turn_attrs["tool_rounds"] = rounds - 1
                    reply = assistant.text.strip()
                    if reply:
                        self.llm.append_assistant(self.history, assistant)
                    else:
                        reply = _RETRY
                    turn_attrs["reply_chars"] = len(reply)
                    return reply

                self.llm.append_assistant(self.history, assistant)
                results: list[ToolResult] = []
                for call in assistant.tool_calls:
                    payload = dispatch(call.name, call.args, model_number=self.model_number)
                    self._observe(call.name, call.args, payload)
                    results.append(ToolResult(call=call, payload=payload))
                self.llm.append_tool_results(self.history, results)

            # Ran out of tool rounds. Do not let the model spin on a live call.
            turn_attrs["tool_rounds"] = rounds
            turn_attrs["outcome"] = "tool_round_limit"
            self.llm.append_user_text(
                self.history,
                "You have used your tool budget for this turn. Answer now from what you "
                "already retrieved, or say you need to transfer. Do not call any more tools.",
            )
            final = self.llm.complete(
                system=self.system_prompt,
                history=self.history,
                tools=None,
                max_tokens=MAX_TURN_TOKENS,
                span_name="agent.model_call.forced_finish",
            )
            reply = final.text.strip() or _TRANSFER
            self.llm.append_assistant(self.history, final)
            return reply
