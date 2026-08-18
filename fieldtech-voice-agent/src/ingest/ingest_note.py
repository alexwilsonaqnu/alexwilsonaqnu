"""Index a ServiceMatters techline note.

These are the cheapest documents in the system and were, until now, the only ones thrown
away. A `techline` hit carries its troubleshooting text *inline* in `techNotes` plus an
explicit `models` array — no PDF, no download, no layout analysis. Across a 59-model fleet
there are 345 of them, and they are exactly the "what actually goes wrong with this one"
knowledge a technician phones in for.

`service_matters_search` returned them live, so the agent could read them in the turn they
were fetched and never again. Writing them into the corpus makes them retrievable next to
the manuals, which is where they belong.

The record shape is the same one `ingest_pdf` produces, so `manual_search` cannot tell the
difference — a note is just a document with one page.
"""

from __future__ import annotations

import re
from typing import Any

from src.ingest.chunker import HAZARD_LEXICON, MAX_CHARS, Chunk
from src.ingest.ingest_pdf import _register_doc, _rewrite_chunks, slugify


def note_doc_id(record: dict[str, Any]) -> str:
    return f"techline_{slugify(record.get('doc_id') or '')}"


def _split(text: str) -> list[str]:
    """Notes are short. Split only when one exceeds a chunk, and split on blank lines."""
    text = (text or "").strip()
    if len(text) <= MAX_CHARS:
        return [text] if text else []
    parts: list[str] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        if buf and len(buf) + len(para) > MAX_CHARS:
            parts.append(buf.strip())
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf.strip():
        parts.append(buf.strip())
    return parts


def ingest_note(record: dict[str, Any], extra_models: list[str] | None = None) -> dict[str, Any]:
    """Write one techline note into the corpus. Returns a small summary."""
    text = record.get("text") or ""
    doc_id = note_doc_id(record)
    title = record.get("doc_title") or "Techline note"
    models = sorted({
        *(m.upper() for m in record.get("models", []) if isinstance(m, str)),
        *(m.upper() for m in (extra_models or [])),
    })

    bodies = _split(text)
    # A note has no banner to key off, so the hazard lexicon is the only signal available —
    # the secondary path the chunker keeps for prose that mentions a hazard outside a
    # banner. `warning_text` stays None: there is no verbatim banner here to quote.
    chunks = [
        Chunk(
            text=body, page=1, section=title, lang="en",
            safety=bool(HAZARD_LEXICON.search(body)),
        ).to_record(doc_id, title, models, index)
        for index, body in enumerate(bodies, start=1)
    ]
    if not chunks:
        return {"doc_id": doc_id, "chunks": 0, "skipped": "empty note"}

    _rewrite_chunks(doc_id, chunks)
    _register_doc(
        {
            "doc_id": doc_id,
            "title": title,
            "models": models,
            "pages": 1,
            "figure_count": 0,
            "category": "Internal Note",
            "source_path": "servicematters:techline",
        }
    )
    return {"doc_id": doc_id, "chunks": len(chunks), "models": len(models)}
