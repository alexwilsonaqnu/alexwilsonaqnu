"""Library research drafter: a Claude Agent SDK agent that turns one seed source into a four-pillar card draft.

Context isolation: each draft is its own `query()` session with a fresh context, no built-in tools
(`tools=[]`, so no shell, files or web search), and exactly two narrow tools served in-process:
  * fetch_source(url)    — the same cached fetch + text extraction the verifier uses, so the drafter quotes
                           the text the verifier will check (public http(s) only; blocked sources refused);
  * search_openalex(q)   — OpenAlex works search, to find the underlying review/trials and their authors.
The answer is returned as structured output validated against `CardDraft`; it is stored as an *unverified*
provisional card. The drafter never decides what is verified or published.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

import httpx

from solbrief.config import get_settings
from solbrief.library.draft import CardDraft
from solbrief.library.seeds import Seed
from solbrief.taxonomy import load_snapshot
from solbrief.verify.fetch import Fetcher, http_fetcher
from solbrief.verify.sources import is_blocked

log = logging.getLogger(__name__)
MAX_TOOL_TEXT = 30_000
OPENALEX = "https://api.openalex.org/works"


@cache
def _skill(name: str) -> str:
    text = resources.files("solbrief.skills").joinpath(f"{name}/SKILL.md").read_text(encoding="utf-8")
    return re.sub(r"\A---.*?---\s*", "", text, flags=re.S)  # drop frontmatter


def system_prompt() -> str:
    _, classes = load_snapshot("v1")
    taxonomy = "\n".join(f"- {c.id}: {c.label}" for c in classes)
    return f"""You research documented responses to US public problems for a solutions-journalism library used by
working reporters. A single invented fact ends their trust, so you work only from sources you fetch.

{_skill("four-pillars")}

{_skill("evidence-grading")}

# How to work
1. Fetch the seed source with fetch_source. Read it fully.
2. Find at least one *independent* source (a different publisher): use search_openalex to find the systematic
   review, trials or evaluations behind the seed, then fetch an open page for one (doi.org link, journal page,
   PubMed abstract, government evaluation report). If you cannot fetch one, still return the card; it will be
   labelled provisional.
3. Every `span` must be copied character-for-character from fetch_source output (40-700 characters). Never
   paraphrase inside a span, never stitch two passages together, never quote from memory.
4. Every number in a claim, in `response_what`, `insight_mechanism` or `insight_transferability` must appear in
   one of your spans for that pillar. If a number isn't quoted, leave it out.
5. Experts: only people named on a page you fetched, with `contact_path_url` set to an https institutional page
   (faculty/staff page or media-relations office) that you fetched and that shows their name. Never personal
   emails or phone numbers. It is fine to return no experts.
6. Use only these taxonomy class ids:
{taxonomy}

Return the card as the structured output. Do not include commentary."""


def _text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def build_tools(fetch: Fetcher):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("fetch_source", "Fetch a public web page or PDF and return its readable text.", {"url": str})
    async def fetch_source(args: dict[str, Any]) -> dict[str, Any]:
        url = str(args.get("url", "")).strip()
        if is_blocked(url):
            return _text_result(f"REFUSED: {url} is a source whose terms forbid automated use. Do not cite it.")
        doc = await asyncio.to_thread(fetch, url)
        if not doc.ok:
            return _text_result(f"UNAVAILABLE: {url} (HTTP {doc.status or 'not fetched'}). Do not cite it.")
        body = doc.text[:MAX_TOOL_TEXT]
        more = "\n[truncated]" if len(doc.text) > MAX_TOOL_TEXT else ""
        return _text_result(f"URL: {url}\n\n{body}{more}")

    @tool(
        "search_openalex", "Search scholarly works (title, year, venue, DOI, open-access URL, authors).", {"query": str}
    )
    async def search_openalex(args: dict[str, Any]) -> dict[str, Any]:
        params = {
            "search": str(args.get("query", ""))[:300],
            "per_page": 5,
            "select": "id,doi,title,publication_year,primary_location,open_access,authorships,type",
        }
        key = get_settings().openalex_api_key
        if key:
            params["api_key"] = key
        try:
            async with httpx.AsyncClient(timeout=30, headers={"User-Agent": get_settings().nws_user_agent}) as c:
                resp = await c.get(OPENALEX, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return _text_result(f"OpenAlex search failed: {exc}")
        works = []
        for w in resp.json().get("results", []):
            works.append(
                {
                    "title": w.get("title"),
                    "year": w.get("publication_year"),
                    "doi": w.get("doi"),
                    "type": w.get("type"),
                    "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                    "open_access_url": (w.get("open_access") or {}).get("oa_url"),
                    "authors": [
                        {
                            "name": (a.get("author") or {}).get("display_name"),
                            "institutions": [i.get("display_name") for i in a.get("institutions", [])],
                        }
                        for a in (w.get("authorships") or [])[:6]
                    ],
                }
            )
        return _text_result(json.dumps(works, indent=1))

    server = create_sdk_mcp_server("library", tools=[fetch_source, search_openalex])
    return server, ["mcp__library__fetch_source", "mcp__library__search_openalex"]


@dataclass
class DraftResult:
    draft: CardDraft | None
    error: str | None = None
    cost_usd: float | None = None
    turns: int | None = None


def seed_prompt(seed: Seed, class_id: str) -> str:
    ratings = seed.notes.get("ratings") if seed.notes else None
    rating_note = (
        f"\nClearinghouse ratings by outcome (carry these hedges through): {json.dumps(ratings)}" if ratings else ""
    )
    return (
        f"Draft one solution card for the problem class `{class_id}`.\n"
        f"Seed source ({seed.publisher}, {seed.source_type}): {seed.title}\n{seed.url}{rating_note}"
    )


async def draft_card(seed: Seed, class_id: str, fetch: Fetcher | None = None, query_fn=None) -> DraftResult:
    """Run one isolated drafting session. `query_fn` is injectable for tests."""
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    s = get_settings()
    server, allowed = build_tools(fetch or http_fetcher())
    with tempfile.TemporaryDirectory(prefix="drafter-") as cwd:
        options = ClaudeAgentOptions(
            tools=[],
            allowed_tools=allowed,
            mcp_servers={"library": server},
            system_prompt=system_prompt(),
            model=s.drafter_model,
            effort="high",
            max_turns=s.drafter_max_turns,
            max_budget_usd=s.drafter_budget_usd,
            output_format={"type": "json_schema", "schema": CardDraft.model_json_schema()},
            permission_mode="dontAsk",
            cwd=cwd,
            env={"ANTHROPIC_API_KEY": s.anthropic_api_key or ""},
        )
        result = None
        async for message in (query_fn or query)(prompt=seed_prompt(seed, class_id), options=options):
            if isinstance(message, ResultMessage):
                result = message
    if result is None:
        return DraftResult(None, "no result message")
    if result.is_error or result.structured_output is None:
        return DraftResult(
            None, f"drafter ended without a card ({result.subtype})", result.total_cost_usd, result.num_turns
        )
    try:
        draft = CardDraft.model_validate(result.structured_output)
    except Exception as exc:
        return DraftResult(None, f"invalid draft: {exc}"[:500], result.total_cost_usd, result.num_turns)
    return DraftResult(draft, None, result.total_cost_usd, result.num_turns)
