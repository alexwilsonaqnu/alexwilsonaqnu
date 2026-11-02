"""Problem classification: deterministic rules first, LLM fallback second.

Order of precedence for a RawItem:
1. Classes the adapter set from the source's own coding (NWS event, FEMA incidentType) — kept as is.
2. Keyword rules over title + summary (below).
3. Optional LLM fallback (Claude, structured output constrained to the taxonomy's class ids, so it cannot
   invent a class). Used only when ANTHROPIC_API_KEY is set; otherwise the item stays unclassified.

Agents never create classes here: an id outside the active taxonomy is impossible by schema and is also
filtered defensively.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from functools import cache

from solbrief.adapters.base import RawItem
from solbrief.config import get_settings
from solbrief.taxonomy import class_ids, load_snapshot

log = logging.getLogger(__name__)

# (pattern, classes). All matching rules contribute; case-insensitive.
KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Enteric pathogens are outbreaks; they're *foodborne* only when a food vehicle is named
    # ("Salmonella Outbreak Linked to Turtles" is not a food-safety story).
    (r"salmonella|e\. ?coli|listeria|botulism|norovirus|cyclospora|hepatitis a", ("infectious-disease-outbreaks",)),
    (r"foodborne|food safety|food recall", ("foodborne-illness",)),
    (r"measles|pertussis|whooping cough|vaccin|immuniz", ("vaccination", "infectious-disease-outbreaks")),
    (r"dengue|west nile|mosquito|\btick|lyme|malaria|chikungunya|zika|oropouche", ("vector-borne-disease",)),
    (r"overdose|opioid|fentanyl|naloxone|substance use|xylazine", ("overdose-substance-use",)),
    (r"suicid", ("suicide-prevention",)),
    (r"heat-related|extreme heat|heat illness", ("extreme-heat",)),
    (r"homeless", ("homelessness",)),
    (r"evict", ("eviction",)),
    (r"maternal|pregnan|prenatal|postpartum|infant|newborn|\bbirth", ("maternal-child-health",)),
    (r"diabetes|cancer|heart disease|hypertension|obesity|stroke\b|chronic disease", ("chronic-disease",)),
    (
        r"lead poisoning|carbon monoxide|toxic|chemical exposure|pfas|arsenic",
        ("environmental-health-exposure",),
    ),
    (
        r"tuberculosis|mycobacteri|streptococc|influenza|covid|sars-cov|hepatitis|\bhiv\b|syphilis|gonorrh"
        r"|candida|legionella|mpox|h5n1|avian flu|outbreak|infection",
        ("infectious-disease-outbreaks",),
    ),
    (r"firearm|gun violence|shooting", ("gun-violence",)),
    (r"motor vehicle|traffic|pedestrian|crash", ("traffic-safety",)),
    (r"mental health|depress|anxiety|psychiatric", ("mental-health",)),
    (r"wildfire|smoke", ("wildfire-smoke",)),
    (r"drinking water|water contamination|water quality", ("water-quality-supply",)),
    (r"air quality|air pollution", ("air-quality",)),
)
_COMPILED = tuple((re.compile(p, re.IGNORECASE), c) for p, c in KEYWORD_RULES)

# A named food vehicle turns an outbreak into a foodborne-illness story.
_FOOD_VEHICLE = re.compile(
    r"\braw\b|cheese|milk|sprouts|oysters|seafood|produce|lettuce|greens|flour|\beggs?\b|chicken|beef|pork|meat"
    r"|cucumbers?|onions?|cantaloupe|fruit|vegetables?|restaurant",
    re.IGNORECASE,
)


def rule_classes(text: str) -> list[str]:
    out: list[str] = []
    for rx, classes in _COMPILED:
        if rx.search(text):
            out.extend(c for c in classes if c not in out)
    if "infectious-disease-outbreaks" in out and "foodborne-illness" not in out and _FOOD_VEHICLE.search(text):
        out.insert(0, "foodborne-illness")
    # "mental-health" is a parent; prefer the youth child when the text is about young people
    if "mental-health" in out and re.search(r"youth|adolescen|student|teen|child", text, re.IGNORECASE):
        out[out.index("mental-health")] = "youth-mental-health"
    return out


# --------------------------------------------------------------------------- LLM fallback


@cache
def _system_prompt() -> str:
    """Stable, cacheable prefix: instructions + the full taxonomy (byte-identical across calls)."""
    _, classes = load_snapshot("v1")
    lines = [f"- {c.id}: {c.label}" + (f" (under {c.parent_id})" if c.parent_id else "") for c in classes]
    return (
        "You classify US public-interest news items (public health reports, alerts, government data releases) "
        "into a fixed problem taxonomy used by a solutions-journalism briefing for reporters.\n\n"
        "Rules:\n"
        "- Choose 1 to 3 class ids that name the underlying problem the item is about.\n"
        "- Prefer the most specific child class; use a parent only when no child fits.\n"
        "- If nothing fits, return an empty list. Never guess loosely.\n"
        "- Use only ids from the taxonomy below.\n\n"
        "Taxonomy (id: label):\n" + "\n".join(lines)
    )


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "class_ids": {"type": "array", "items": {"type": "string", "enum": sorted(class_ids())}},
        },
        "required": ["class_ids"],
        "additionalProperties": False,
    }


LLMClassifier = Callable[[str], list[str]]


def anthropic_classifier() -> LLMClassifier | None:
    """Build the Claude-backed classifier, or None when no API key is configured."""
    s = get_settings()
    if not s.anthropic_api_key:
        return None
    import anthropic

    client = anthropic.Anthropic(api_key=s.anthropic_api_key)

    def classify(text: str) -> list[str]:
        response = client.messages.create(
            model=s.classifier_model,
            max_tokens=4096,
            system=[{"type": "text", "text": _system_prompt(), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Item:\n{text}"}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema()}},
        )
        if response.stop_reason == "refusal":
            log.warning("classifier refused item: %.80s", text)
            return []
        body = next((b.text for b in response.content if b.type == "text"), "")
        try:
            ids = json.loads(body).get("class_ids", [])
        except json.JSONDecodeError:
            log.warning("classifier returned invalid JSON (stop_reason=%s)", response.stop_reason)
            return []
        return ids

    return classify


def classify_items(items: list[RawItem], llm: LLMClassifier | None = None) -> dict[str, int]:
    """Fill `class_ids` in place. Returns counts by method."""
    valid = class_ids()
    counts = {"source_coded": 0, "rules": 0, "llm": 0, "unclassified": 0}
    for it in items:
        if it.class_ids:
            it.class_ids = [c for c in it.class_ids if c in valid]
            counts["source_coded"] += 1
            continue
        text = f"{it.title}\n{it.summary or ''}".strip()
        found = rule_classes(text)
        method = "rules"
        if not found and llm is not None:
            try:
                found = [c for c in llm(text) if c in valid][:3]
                method = "llm"
            except Exception:  # an LLM outage must not fail ingest
                log.exception("LLM classification failed; leaving item unclassified")
                found = []
        it.class_ids = found
        counts[method if found else "unclassified"] += 1
    return counts
