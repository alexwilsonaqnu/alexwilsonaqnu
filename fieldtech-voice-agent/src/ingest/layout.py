"""Layout-aware text extraction for service documents.

Why this exists: the real Whirlpool Tech Sheet (W11428598) is an 11x17 sheet with
English, French and Spanish set in parallel columns, and the columns are *regional* —
there is no page-wide gutter to split on. Reading the page with a naive text extractor
produces language-salad:

    Only authorized technicians should perform Seul un technicien autorisé est
    habilité à effectuer des Las mediciones de voltaje para diagnóstico deberán

Speaking that aloud as a quoted safety warning is the single worst failure this system
could have, so extraction has to understand blocks before it understands words.

What this does, in order:
  1. group words into lines
  2. split each line into runs at horizontal gaps (a run is one column's worth of text)
  3. grow runs into blocks by vertical adjacency and horizontal overlap
  4. order blocks in reading order — band by band, left to right
  5. score each block's language lexically, inheriting from neighbours when too short
  6. classify blocks as heading / safety / body

Seam: this is a local stand-in for Document AI Layout Parser, which does the same job
properly (and handles tables, which this does not). The Block contract is what the rest
of ingestion consumes, so swapping the implementation is a one-file change.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable

# --- language scoring --------------------------------------------------------
# Deliberately distinctive tokens: "de" and "la" are shared by French and Spanish and
# carry no signal, so they are absent here.
_MARKERS: dict[str, set[str]] = {
    "en": {"the", "and", "of", "to", "for", "with", "is", "are", "not", "this", "before",
           "after", "must", "should", "can", "will", "from", "these", "instructions",
           "power", "check", "replace", "failure", "follow", "result", "if", "when"},
    "fr": {"le", "les", "des", "du", "est", "aux", "avec", "pour", "sur", "ne", "pas",
           "vous", "être", "doit", "peut", "avant", "après", "toute", "sont", "une",
           "instructions", "puissance", "vérifier", "remplacer", "非"},
    "es": {"el", "los", "las", "del", "para", "con", "que", "por", "una", "debe",
           "puede", "antes", "después", "todo", "son", "no", "las", "instrucciones",
           "seguir", "puede", "verificar", "reemplazar"},
}
_DIACRITIC_HINTS = [("es", "ñ¿¡"), ("fr", "çœàèêûù"), ("es", "áíóú")]
_TOKEN_RE = re.compile(r"[a-zà-öø-ÿñ]+", re.IGNORECASE)


def detect_language(text: str) -> tuple[str, float]:
    """Return (lang, confidence 0-1). 'unknown' when there is too little to go on."""
    tokens = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    if len(tokens) < 4:
        return "unknown", 0.0
    scores = {lang: sum(1 for t in tokens if t in markers) for lang, markers in _MARKERS.items()}
    lowered = (text or "").lower()
    for lang, chars in _DIACRITIC_HINTS:
        scores[lang] += sum(2 for c in chars if c in lowered)
    best = max(scores, key=lambda k: scores[k])
    total = sum(scores.values())
    if total == 0:
        return "unknown", 0.0
    return best, scores[best] / total


# --- blocks ------------------------------------------------------------------
SAFETY_BANNER = re.compile(r"^\s*(DANGER|WARNING|CAUTION|AVERTISSEMENT|ADVERTENCIA|PELIGRO|MISE EN GARDE)\b", re.I)
# Em/en dashes and colons are in here because service-document headings are written
# "SECTION 4 — COMPONENT ACCESS AND ELECTRICAL SAFETY". Leaving them out silently
# demoted every section heading to body text, which cost the chunks their `section`.
HEADING_RE = re.compile(r"^[A-Z0-9][A-Z0-9 ,./&'\-()—–:]{5,70}$")


@dataclass
class Block:
    text: str
    page: int
    x0: float
    x1: float
    top: float
    bottom: float
    lang: str = "unknown"
    lang_confidence: float = 0.0
    kind: str = "body"          # body | heading | safety
    lines: list[str] = field(default_factory=list)

    @property
    def width(self) -> float:
        return self.x1 - self.x0


def _lines_from_words(words: list[dict[str, Any]], tol: float) -> list[list[dict[str, Any]]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for w in words:
        buckets.setdefault(int(w["top"] / tol), []).append(w)
    return [sorted(ws, key=lambda w: w["x0"]) for _, ws in sorted(buckets.items())]


def _split_runs(line: list[dict[str, Any]], gap: float) -> list[list[dict[str, Any]]]:
    """A gap wider than `gap` inside a line is a column boundary, not a word space."""
    runs, current = [], [line[0]]
    for prev, word in zip(line, line[1:]):
        if word["x0"] - prev["x1"] > gap:
            runs.append(current)
            current = [word]
        else:
            current.append(word)
    runs.append(current)
    return runs


def _run_box(run: list[dict[str, Any]]) -> tuple[str, float, float, float, float]:
    return (
        " ".join(w["text"] for w in run),
        min(w["x0"] for w in run),
        max(w["x1"] for w in run),
        min(w["top"] for w in run),
        max(w["bottom"] for w in run),
    )


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Overlap as a fraction of the WIDER span, so a narrow column run cannot be
    absorbed by a full-page-width block. Dividing by the narrower span instead lets a
    page-spanning header swallow every column beneath it — which is exactly how the
    trilingual columns collapsed back into one block."""
    span = min(a1, b1) - max(a0, b0)
    return span / max(1.0, max(a1 - a0, b1 - b0))


ROTATED_PAGE_RATIO = 0.6    # above this share of sideways chars, read the page rotated


def _rotated_words(page) -> list[dict[str, Any]]:
    """Re-read a sideways page along its own reading axis.

    Page 2 of the W11428598 Tech Sheet is the wiring schematic, set 90 degrees to the
    sheet. Every character on it is non-upright, so reading it in page coordinates walks
    across the text instead of along it and yields mirrored nonsense — 'ROTICAPAC' for
    CAPACITOR, 'RUETOM' for MOTEUR. Rotating the coordinate frame first recovers the real
    content, which on this page is the connector pin table (J4-1, J4-2, lid switch states)
    — the most useful thing on the sheet for a technician with a meter in their hand.
    """
    height = page.height
    remapped = []
    for char in page.chars:
        # Space glyphs are kept: they are the only word boundaries this document has.
        # A true 90-degree rotation: (x, y) -> (H - y, x). Deriving the vertical axis from
        # `width - x1` instead looks right line by line but is a reflection, and it silently
        # reverses row order — the voltage-check procedure came out numbered 4, 3, 2, 1.
        remapped.append(
            {
                "text": char["text"],
                "x0": height - char["bottom"],
                "x1": height - char["top"],
                "top": char["x0"],
                "bottom": char["x1"],
            }
        )
    return _words_from_chars(remapped)


def _words_from_chars(chars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group characters into words, splitting on space glyphs and on wide gaps.

    This Tech Sheet positions its glyphs with **zero** advance between them — the gap
    between the 'y' of "Only" and the 'a' of "authorized" measures 0.00 points, exactly
    like the gap inside "authorized". Word boundaries therefore cannot be recovered from
    geometry at all; they exist only as explicit space characters in the content stream.
    Dropping those spaces before measuring (the obvious way to write this) welds every
    line into one token and takes language detection down with it.

    So: split on a space glyph, and additionally on a gap wide enough to be a real
    separator, which is what catches table cells set flush against each other. Column
    gutters are left to `_split_runs`, which knows the page's own gutter width.
    """
    if not chars:
        return []
    char_h = statistics.median(c["bottom"] - c["top"] for c in chars) or 10.0
    words: list[dict[str, Any]] = []
    for _, row in sorted(_bucket(chars, key=lambda c: c["top"], tol=max(2.0, char_h * 0.6)).items()):
        row.sort(key=lambda c: c["x0"])
        widths = [c["x1"] - c["x0"] for c in row if c["x1"] > c["x0"]]
        gap_split = max(0.9 * (statistics.median(widths) if widths else 5.0), 1.5)
        current: list[dict[str, Any]] = []
        for prev, char in zip([None, *row], row):
            if not char["text"].strip():
                if current:
                    words.append(_join_chars(current))
                    current = []
                continue
            if current and prev is not None and char["x0"] - prev["x1"] > gap_split:
                words.append(_join_chars(current))
                current = []
            current.append(char)
        if current:
            words.append(_join_chars(current))
    return words


def _bucket(items, key, tol: float) -> dict[int, list]:
    out: dict[int, list] = {}
    for item in items:
        out.setdefault(int(key(item) / tol), []).append(item)
    return out


def _join_chars(chars: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "text": "".join(c["text"] for c in chars),
        "x0": min(c["x0"] for c in chars),
        "x1": max(c["x1"] for c in chars),
        "top": min(c["top"] for c in chars),
        "bottom": max(c["bottom"] for c in chars),
    }


def page_is_rotated(page) -> bool:
    chars = page.chars
    if not chars:
        return False
    sideways = sum(1 for c in chars if not c.get("upright", True))
    return sideways / len(chars) >= ROTATED_PAGE_RATIO


def _same_column(x0: float, x1: float, blk: dict[str, Any], char_w: float) -> bool:
    """A short final line of a paragraph shares the block's left margin and fits inside it.

    The overlap test alone measures against the wider span, so the last line of a
    justified paragraph — "electrical shock.", 70pt under a 196pt block — scores 0.36 and
    detaches. When that paragraph is a safety banner the cost is a `warning_text` the
    agent would read aloud as "Failure to do so can result in death or", stopping there.
    A run at the same left margin and no wider than the block is a continuation, not a
    neighbouring column: a real column starts at a visibly different x0.
    """
    return abs(x0 - blk["x0"]) <= 1.5 * char_w and x1 <= blk["x1"] + char_w


def extract_blocks(page, page_number: int) -> list[Block]:
    """Segment one pdfplumber page into reading-ordered, language-tagged blocks."""
    if page_is_rotated(page):
        words = _rotated_words(page)
    else:
        words = _words_from_chars(page.chars)
    if not words:
        return []

    heights = [w["bottom"] - w["top"] for w in words]
    line_h = statistics.median(heights) or 10.0
    widths = [(w["x1"] - w["x0"]) / max(1, len(w["text"])) for w in words if w["text"]]
    char_w = statistics.median(widths) or 5.0
    col_gap = max(3.0 * char_w, 14.0)      # a column gutter, not a word space
    row_gap = 1.7 * line_h                  # blocks break across a blank-ish line

    open_blocks: list[dict[str, Any]] = []
    done: list[Block] = []

    for line in _lines_from_words(words, tol=max(2.0, line_h * 0.6)):
        for run in _split_runs(line, col_gap):
            text, x0, x1, top, bottom = _run_box(run)
            if not text.strip():
                continue
            # attach to the open block that overlaps horizontally and is vertically near
            target = None
            for blk in open_blocks:
                if top - blk["bottom"] > row_gap:
                    continue
                if _overlap(x0, x1, blk["x0"], blk["x1"]) > 0.45 or _same_column(
                    x0, x1, blk, char_w
                ):
                    target = blk
                    break
            if target is None:
                open_blocks.append(
                    {"lines": [text], "x0": x0, "x1": x1, "top": top, "bottom": bottom}
                )
            else:
                target["lines"].append(text)
                target["x0"] = min(target["x0"], x0)
                target["x1"] = max(target["x1"], x1)
                target["bottom"] = max(target["bottom"], bottom)

    # Blocks are not retired as the cursor advances: a taller neighbouring column pushes
    # the cursor past a shorter block and would close it mid-paragraph, which is what
    # truncated the English safety text to three lines while French kept eight. The
    # attach test above already refuses anything vertically distant, so leaving blocks
    # open is safe. Cost is O(lines x open blocks), fine at service-document scale.
    done.extend(_finalize(blk, page_number) for blk in open_blocks)

    # reading order: banded top-to-bottom, then left-to-right inside a band
    done.sort(key=lambda b: (round(b.top / (line_h * 3)), b.x0))
    _inherit_language(done)
    return done


def _finalize(blk: dict[str, Any], page_number: int) -> Block:
    text = "\n".join(blk["lines"]).strip()
    lang, conf = detect_language(text)
    first = (blk["lines"][0] if blk["lines"] else "").strip()
    if SAFETY_BANNER.match(first):
        kind = "safety"
    elif len(blk["lines"]) == 1 and HEADING_RE.match(first):
        kind = "heading"
    else:
        kind = "body"
    return Block(
        text=text, page=page_number, x0=blk["x0"], x1=blk["x1"],
        top=blk["top"], bottom=blk["bottom"], lang=lang, lang_confidence=conf,
        kind=kind, lines=list(blk["lines"]),
    )


def _inherit_language(blocks: list[Block]) -> None:
    """Short blocks (a heading, a banner word) carry no lexical signal. Give them the
    language of the nearest confidently-scored block sharing their column."""
    scored = [b for b in blocks if b.lang != "unknown" and b.lang_confidence >= 0.5]
    if not scored:
        return
    for b in blocks:
        if b.lang != "unknown" and b.lang_confidence >= 0.5:
            continue
        near = min(
            scored,
            key=lambda s: (abs(s.x0 - b.x0) * 1.0) + (abs(s.top - b.top) * 0.35),
        )
        b.lang = near.lang
        b.lang_confidence = 0.0  # inherited, not measured


def english_blocks(blocks: Iterable[Block]) -> list[Block]:
    return [b for b in blocks if b.lang == "en"]
