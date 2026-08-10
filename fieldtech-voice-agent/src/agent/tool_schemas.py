"""Gemini function declarations for the five tools.

Declarations use `parameters_json_schema` (plain JSON Schema). The SDK passes function
declarations through to the Vertex payload verbatim as `functionDeclarations`, so this is
the shape the Model Garden endpoint receives.
"""

from __future__ import annotations

from typing import Any

from google.genai import types

SALESFORCE_LOOKUP = types.FunctionDeclaration(
    name="salesforce_lookup",
    description=(
        "Look up a field technician by their technician id and return their open service "
        "case, including the appliance model number, serial number and reported issue. "
        "Call this first whenever the technician gives an id like T-1001. Returns "
        "open_case = null for unknown technicians, in which case ask for the model number."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "technician_id": {
                "type": "string",
                "description": "Technician id as spoken, e.g. 'T-1001'.",
            }
        },
        "required": ["technician_id"],
    },
)

SERVICE_MATTERS_SEARCH = types.FunctionDeclaration(
    name="service_matters_search",
    description=(
        "Stage 1 retrieval. Given an appliance model number, return the candidate service "
        "document ids that cover it. Call this after you have a confirmed model number and "
        "before manual_search, then pass the returned doc_ids into manual_search."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "model_number": {
                "type": "string",
                "description": "Full or partial appliance model number, e.g. 'WTW5057LW0'.",
            }
        },
        "required": ["model_number"],
    },
)

MANUAL_SEARCH = types.FunctionDeclaration(
    name="manual_search",
    description=(
        "Stage 2 retrieval. Search inside service manual pages and return passages with "
        "doc id, page number, text, figure ids and a safety flag. Restrict to the doc_ids "
        "from service_matters_search when you have them. Every instruction you give the "
        "technician must come from a passage returned by this tool."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query. Prefer service manual vocabulary over the technician's "
                    "phrasing, e.g. 'basket does not agitate shifter' rather than 'it's broken'."
                ),
            },
            "doc_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Candidate doc ids from service_matters_search. Omit to search everything.",
            },
            "k": {
                "type": "integer",
                "description": "Number of passages to return. Default 5.",
            },
        },
        "required": ["query"],
    },
)

GET_FIGURE = types.FunctionDeclaration(
    name="get_figure",
    description=(
        "Push a diagram, schematic or exploded view to the technician's mobile app. Call "
        "this whenever a retrieved passage references a figure the technician needs to see. "
        "Diagrams cannot be spoken, so never describe one — push it and say you have."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "doc_id": {"type": "string", "description": "Document id the figure belongs to."},
            "figure_id": {
                "type": "string",
                "description": "Figure id exactly as listed in the passage's figures array.",
            },
        },
        "required": ["doc_id", "figure_id"],
    },
)

SALESFORCE_WRITEBACK = types.FunctionDeclaration(
    name="salesforce_writeback",
    description=(
        "Append a resolution summary to the Salesforce case at the end of a call. Include "
        "what was diagnosed, which steps were completed, and any part that needs ordering."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "case_id": {"type": "string", "description": "Case id from salesforce_lookup."},
            "resolution_summary": {
                "type": "string",
                "description": "Plain-text summary of the diagnosis and the outcome.",
            },
        },
        "required": ["case_id", "resolution_summary"],
    },
)

ORCHESTRATOR_DECLARATIONS = [
    SALESFORCE_LOOKUP,
    SERVICE_MATTERS_SEARCH,
    MANUAL_SEARCH,
    GET_FIGURE,
    SALESFORCE_WRITEBACK,
]

# The evaluator subagent gets manual_search and nothing else.
EVALUATOR_DECLARATIONS = [MANUAL_SEARCH]


def orchestrator_tools() -> list[types.Tool]:
    return [types.Tool(function_declarations=ORCHESTRATOR_DECLARATIONS)]


def evaluator_tools() -> list[types.Tool]:
    return [types.Tool(function_declarations=EVALUATOR_DECLARATIONS)]


VERDICT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["ANSWERABLE", "NEEDS_RERETRIEVAL", "ESCALATE"],
        },
        "reasoning": {"type": "string"},
        "suggested_query": {"type": "string"},
    },
    "required": ["verdict", "reasoning", "suggested_query"],
}
