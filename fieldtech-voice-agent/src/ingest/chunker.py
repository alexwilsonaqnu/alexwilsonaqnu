"""Group layout blocks into retrievable chunks.

Page-level chunking does not survive a real service document: the WTW5057LW0 Tech Sheet
is 22,673 characters on page one, so every query returns "page 1" and BM25 cannot
discriminate at all. Sections are the unit that matches how a technician asks a question
and how the document is written.

Rules, in order of precedence:
  * a safety banner (DANGER / WARNING / CAUTION) always starts its own chunk, and its
    text is preserved verbatim in `warning_text` — that is the string the agent quotes,
    so nothing may be merged into it or reflowed
  * a heading starts a new chunk
  * a chunk closes when it reaches MAX_CHARS
  * chunks below MIN_CHARS are merged forward, so a stray one-line fragment does not
    become its own retrievable unit

`safety` is now structural — it comes from the banner block, not from a keyword search
over page text. The regex lexicon survives only as a secondary signal for prose that
mentions a hazard outside a banner.

A banner also **governs** the procedure printed under it, and that governance is carried
into the chunks (`_propagate_banners`). Splitting the banner into its own chunk is right
for quoting and wrong for retrieval on its own: "WARNING: Electrical Shock Hazard.
Disconnect power before servicing." shares no terms with "what do I check before I open
the console", so the banner chunk scores 0.0 on exactly the question that needs it, while
the procedure chunk ranks first carrying no warning at all. Every chunk under a banner
therefore repeats it — verbatim, at the top of the text and in `warning_text`. Duplicating
a warning is the safe direction to be wrong in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.ingest.layout import Block

MIN_CHARS = 220
MAX_CHARS = 1800

# Secondary signal only. A banner block is the primary, structural one.
HAZARD_LEXICON = re.compile(
    r"warning|danger|caution|electrical shock|disconnect power|unplug|capacitor|gas|"
    r"scald|sharp edge|high voltage|live circuit",
    re.IGNORECASE,
)


# A banner governs the procedure printed under it, not the rest of the sheet. Without a
# geometric bound one WARNING on a dense Tech Sheet claimed every chunk on the page, which
# makes `safety` mean nothing — the flag has to stay rare enough to be a signal.
GOVERN_COLUMN_OVERLAP = 0.5   # governed text must sit under the banner's column
GOVERN_MAX_GAP = 90.0         # points below the banner; roughly one short procedure


@dataclass
class Chunk:
    text: str
    page: int
    section: str | None = None
    safety: bool = False
    warning_text: str | None = None
    lang: str = "en"
    figures: list[str] = field(default_factory=list)
    x0: float = 0.0
    x1: float = 0.0
    top: float = 0.0
    bottom: float = 0.0

    def to_record(self, doc_id: str, doc_title: str, models: list[str], index: int) -> dict[str, Any]:
        return {
            "chunk_id": f"{doc_id}_c{index}",
            "doc_id": doc_id,
            "doc_title": doc_title,
            "models": models,
            "page": self.page,
            "section": self.section,
            "lang": self.lang,
            "text": self.text,
            "warning_text": self.warning_text,
            "figures": self.figures,
            "safety": self.safety,
        }


def _flush(buf: list[Block], section: str | None) -> Chunk | None:
    if not buf:
        return None
    text = "\n".join(b.text for b in buf).strip()
    if not text:
        return None
    banner = next((b for b in buf if b.kind == "safety"), None)
    body = text if section is None else f"{section}\n{text}"
    return Chunk(
        text=body,
        page=buf[0].page,
        section=section,
        safety=banner is not None or bool(HAZARD_LEXICON.search(text)),
        warning_text=banner.text if banner else None,
        lang=buf[0].lang,
        **_box(buf),
    )


def _box(blocks: list[Block]) -> dict[str, float]:
    return {
        "x0": min(b.x0 for b in blocks),
        "x1": max(b.x1 for b in blocks),
        "top": min(b.top for b in blocks),
        "bottom": max(b.bottom for b in blocks),
    }


def chunk_blocks(blocks: list[Block]) -> list[Chunk]:
    """Blocks in reading order -> section-sized chunks."""
    chunks: list[Chunk] = []
    buf: list[Block] = []
    section: str | None = None
    size = 0

    def flush() -> None:
        nonlocal buf, size
        chunk = _flush(buf, section)
        if chunk:
            chunks.append(chunk)
        buf, size = [], 0

    for block in blocks:
        if block.kind == "safety":
            # A banner is never merged with surrounding prose — the quote must stay exact.
            flush()
            chunks.append(
                Chunk(
                    text=block.text,
                    page=block.page,
                    section=section,
                    safety=True,
                    warning_text=block.text,
                    lang=block.lang,
                    **_box([block]),
                )
            )
            continue

        if block.kind == "heading":
            flush()
            section = block.text.strip()
            continue

        if size + len(block.text) > MAX_CHARS and buf:
            flush()

        buf.append(block)
        size += len(block.text)

    flush()
    return _propagate_banners(_merge_small(chunks))


def _propagate_banners(chunks: list[Chunk]) -> list[Chunk]:
    """Carry each banner into the chunks it governs.

    Scope is geometric, not just ordinal: the governed chunk must be on the same page, in
    the same section, sitting in the banner's column and within GOVERN_MAX_GAP of it. That
    is how the document is laid out — the DANGER block sits at the head of the procedure it
    applies to — and the bound is what keeps `safety` a signal instead of a page-wide
    default.
    """
    governing: Chunk | None = None
    for chunk in chunks:
        if chunk.warning_text and chunk.text == chunk.warning_text:
            governing = chunk           # the banner itself; nothing to prepend
            continue
        if governing is None or not _governs(governing, chunk):
            governing = None
            continue
        chunk.safety = True
        chunk.warning_text = governing.warning_text
        chunk.text = f"{governing.warning_text}\n{chunk.text}"
    return chunks


def _governs(banner: Chunk, chunk: Chunk) -> bool:
    if chunk.page != banner.page or chunk.section != banner.section:
        return False
    if not 0 <= chunk.top - banner.bottom <= GOVERN_MAX_GAP:
        return False
    span = min(chunk.x1, banner.x1) - max(chunk.x0, banner.x0)
    return span / max(1.0, min(chunk.x1 - chunk.x0, banner.x1 - banner.x0)) >= GOVERN_COLUMN_OVERLAP


def _merge_small(chunks: list[Chunk]) -> list[Chunk]:
    """Fold undersized non-safety chunks into the next one on the same page."""
    out: list[Chunk] = []
    for chunk in chunks:
        if (
            out
            and not chunk.safety
            and not out[-1].safety
            and len(out[-1].text) < MIN_CHARS
            and out[-1].page == chunk.page
            and len(out[-1].text) + len(chunk.text) <= MAX_CHARS
        ):
            merged = out[-1]
            merged.text = f"{merged.text}\n{chunk.text}"
            merged.safety = merged.safety or chunk.safety
            continue
        out.append(chunk)
    return out
