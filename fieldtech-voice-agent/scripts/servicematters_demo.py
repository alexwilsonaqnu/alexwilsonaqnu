"""ServiceMatters routing with an LLM on top of it — the retrieval layer, on its own.

    python scripts/servicematters_demo.py --model WTW5057LW0 \
        --ask "the washer fills but will not agitate, what do I check first"

This is the full agent with everything else taken away: no Salesforce, no voice, no turn
shaping, no tools the model can call. One model number goes in, ServiceMatters says which
documents exist for it, and the answer is grounded in those documents and nothing else.

Five stages, each printed, because the point of the mockup is to *show* the basis:

  1. ROUTE     live ServiceMatters call -> components ranked by category
  2. GROUND    pick the grounding document — Tech Sheet first, always
  3. INGEST    fetch that PDF and run it through the layout pipeline (cached)
  4. RETRIEVE  passages from the chosen document + inline techline notes
  5. ANSWER    one model call over exactly that pack, with citations

Stages 1-4 are keyless. Stage 5 needs ANTHROPIC_API_KEY; without one the script prints
the grounding pack and stops rather than inventing an answer.

Why the Tech Sheet outranks everything: ServiceMatters' own `_score` is ~4e-05 for every
hit on a model query, so relevance ranking is unavailable and category is the only signal
there is. On a diagnostic call a Tech Sheet carries the fault-code table and the component
tests; an Owner's Manual carries the wash-cycle chart. See CATEGORY_RANK in
src/tools/service_matters.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import DATA_DIR, DOC_REGISTRY_PATH, HTTP_TIMEOUT_SECONDS  # noqa: E402
from src.tools.manual_search import manual_search  # noqa: E402
from src.tools.service_matters import service_matters_search  # noqa: E402

MAX_NOTES = 3          # inline techline notes to put in the pack
MAX_PASSAGES = 5

SYSTEM = """You are FieldTech Assist, answering a Whirlpool field technician who is \
standing at the appliance.

You have been given a grounding pack: the service documents ServiceMatters lists for this \
model, passages retrieved from the highest-ranked one, and any inline techline notes. \
That pack is the whole of your knowledge. Rules:

- Every instruction you give must come from a passage in the pack. If the pack does not \
support an answer, say so plainly and say which document the technician should pull \
instead. Never fill a gap from general appliance knowledge.
- Cite the document and page the first time you use it, the way a person would: "that's \
from the Tech Sheet, page one".
- Quote any WARNING, DANGER or CAUTION verbatim, word for word, and never paraphrase or \
summarise one.
- A fault code means what the pack says it means. Do not infer a code's meaning from its \
number.
- Answer in a few sentences. This is a phone call, not a document."""


# --- stage 1: route ----------------------------------------------------------
def route(model: str, k: int) -> dict[str, Any]:
    result = service_matters_search(model, k=k)
    print(f"\n[1] ROUTE  ServiceMatters q={model}")
    if result["fell_back"]:
        print(f"    !! fell back to {result['source']}: {result['fallback_reason']}")
    else:
        print(
            f"    {result['total_available']} hits, "
            f"{result['excluded_consumer_docs']} consumer docs excluded"
        )
    print(f"    {'rank':<5}{'category':<20}{'doc id':<12}title")
    for record in result["results"]:
        print(
            f"    {record['rank']:<5}{record['category'][:19]:<20}"
            f"{record['doc_id']:<12}{str(record.get('doc_title'))[:44]}"
        )
    for note in result["notes"][:MAX_NOTES]:
        confirmed = {True: "model confirmed", False: "other model", None: "coverage unstated"}[
            note.get("model_confirmed")
        ]
        print(f"    note  {'Internal Note':<20}{'(inline)':<12}"
              f"{str(note.get('doc_title'))[:44]}  [{confirmed}]")
    return result


# --- stage 2 + 3: ground and ingest -----------------------------------------
def _registry() -> list[dict[str, Any]]:
    if not DOC_REGISTRY_PATH.exists():
        return []
    return json.loads(DOC_REGISTRY_PATH.read_text(encoding="utf-8")).get("docs", [])


def _already_ingested(doc_id: str) -> str | None:
    """ServiceMatters ids are lit part numbers (W11428598); ours are file slugs."""
    needle = doc_id.lower()
    for doc in _registry():
        if needle in doc["doc_id"].lower():
            return doc["doc_id"]
    return None


def ground(result: dict[str, Any], model: str) -> tuple[str | None, dict[str, Any] | None]:
    """Pick the grounding document and make sure it is ingested."""
    candidates = result["results"]
    if not candidates:
        print("\n[2] GROUND  no service documents returned — nothing to ground on")
        return None, None

    chosen = candidates[0]          # already sorted by category rank
    print(f"\n[2] GROUND  {chosen['category']} {chosen['doc_id']} — "
          f"rank {chosen['rank']}, best of {len(candidates)}")

    local_id = _already_ingested(chosen["doc_id"])
    if local_id:
        print(f"[3] INGEST  cached, reusing {local_id}")
        return local_id, chosen

    url = chosen.get("url")
    if not url:
        print("[3] INGEST  no attachment url on that record — cannot ground")
        return None, chosen

    slug = re.sub(r"[^a-z0-9]+", "_", chosen["category"].lower()).strip("_")
    pdf_path = DATA_DIR / f"{chosen['doc_id'].lower()}_{slug}.pdf"
    print(f"[3] INGEST  fetching {url}")
    try:
        import httpx

        response = httpx.get(url, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(response.content)
    except Exception as exc:
        print(f"[3] INGEST  download failed: {type(exc).__name__}: {exc}")
        return None, chosen

    from src.ingest.ingest_pdf import ingest

    summary = ingest(pdf_path, [model.upper()], title=chosen.get("doc_title"))
    print(f"    {pdf_path.name}  {summary['pages']}p -> {summary['chunks']} chunks, "
          f"{summary['safety_chunks']} safety, "
          f"{summary['blocks_dropped_by_language'] or 'monolingual'}")
    return summary["doc_id"], chosen


# --- stage 4: retrieve -------------------------------------------------------
def build_pack(
    question: str, doc_id: str | None, routing: dict[str, Any], chosen: dict[str, Any] | None
) -> str:
    lines = [
        "AVAILABLE SERVICE DOCUMENTS (ServiceMatters, ranked by category):",
        *(
            f"  - {r['category']}: {r['doc_title']} [{r['doc_id']}]"
            for r in routing["results"]
        ),
    ]

    passages: list[dict[str, Any]] = []
    if doc_id:
        passages = manual_search(question, doc_ids=[doc_id], k=MAX_PASSAGES)["results"]
        lines.append(
            f"\nPASSAGES RETRIEVED FROM {chosen['category']} {chosen['doc_id']} "
            f"({chosen.get('doc_title')}):"
        )
        for hit in passages:
            flag = "  [SAFETY — quote verbatim]" if hit["safety"] else ""
            lines.append(f"\n--- page {hit['page']}{flag}\n{hit['text']}")

    notes = routing["notes"][:MAX_NOTES]
    if notes:
        lines.append("\nINLINE TECHLINE NOTES (no PDF; text as published):")
        for note in notes:
            lines.append(f"\n--- {note.get('doc_title')}\n{(note.get('text') or '').strip()}")

    print(f"\n[4] RETRIEVE  {len(passages)} passages"
          f"{' (%d safety)' % sum(1 for p in passages if p['safety']) if passages else ''}"
          f", {len(notes)} inline notes")
    return "\n".join(lines)


# --- stage 5: answer ---------------------------------------------------------
def answer(question: str, pack: str) -> int:
    from src.agent.llm import get_llm
    from src.agent.llm.base import LLMConfigError

    try:
        client = get_llm("orchestrator")
        ok, detail = client.preflight()
    except LLMConfigError as exc:
        print(f"\n[5] ANSWER  not configured: {exc}")
        return 2
    if not ok:
        print(f"\n[5] ANSWER  brain unreachable: {detail}\n{client.remediation()}")
        return 2

    history = client.new_history()
    client.append_user_text(history, f"{pack}\n\nTECHNICIAN ASKS: {question}")
    turn = client.complete(system=SYSTEM, history=history, max_tokens=1024,
                           span_name="servicematters_demo.answer")
    if turn.refused:
        print("\n[5] ANSWER  the model declined this request")
        return 1
    print(f"\n[5] ANSWER  ({client.provider} {client.model})\n")
    print(textwrap.indent(textwrap.fill(turn.text.strip(), 86), "    "))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="WTW5057LW0", help="appliance model number")
    parser.add_argument("--ask", default="the washer fills but will not agitate, "
                                         "what should I check first")
    parser.add_argument("-k", type=int, default=6, help="documents to consider")
    parser.add_argument("--show-pack", action="store_true",
                        help="print the grounding pack sent to the model")
    args = parser.parse_args(argv)

    routing = route(args.model, args.k)
    doc_id, chosen = ground(routing, args.model)
    pack = build_pack(args.ask, doc_id, routing, chosen)
    if args.show_pack:
        print("\n--- GROUNDING PACK ---\n" + pack + "\n--- END PACK ---")
    if not doc_id and not routing["notes"]:
        print("\nNothing to ground on — refusing to answer.")
        return 1
    return answer(args.ask, pack)


if __name__ == "__main__":
    raise SystemExit(main())
