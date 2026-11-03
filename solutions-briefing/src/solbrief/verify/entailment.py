"""Optional semantic check: does the quoted span actually support the claim?

Runs only after every deterministic check has passed, in its own API call (no drafter context), and can only
*drop* a claim. A refusal, malformed answer or outage counts as "not supported" upstream.
"""

from __future__ import annotations

import json
import logging

from solbrief.config import get_settings
from solbrief.verify.card import EntailmentCheck

log = logging.getLogger(__name__)

SYSTEM = (
    "You check quotations for a fact-checking pipeline used by journalists. Given a CLAIM and a verbatim SPAN "
    "from a source, answer whether the span, read on its own, fully supports the claim. Answer supported=false "
    "if the claim adds facts, overstates certainty or effect size, generalizes beyond the population or place in "
    'the span, changes a hedge ("potentially", "mixed", "no discernible"), or reverses the meaning. When in '
    "doubt, answer false."
)
SCHEMA = {
    "type": "object",
    "properties": {"supported": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["supported", "reason"],
    "additionalProperties": False,
}


def anthropic_entailment() -> EntailmentCheck | None:
    s = get_settings()
    if not (s.anthropic_api_key and s.verifier_entailment):
        return None
    import anthropic

    client = anthropic.Anthropic(api_key=s.anthropic_api_key)

    def entails(claim: str, span: str) -> bool:
        response = client.beta.messages.create(
            model=s.verifier_model,
            max_tokens=4096,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"CLAIM:\n{claim}\n\nSPAN:\n{span}"}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            log.warning("entailment check refused; treating claim as unsupported")
            return False
        body = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return bool(json.loads(body)["supported"])
        except (json.JSONDecodeError, KeyError, TypeError):
            log.warning("entailment check returned malformed output; treating claim as unsupported")
            return False

    return entails
