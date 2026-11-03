"""Deterministic claim/span checks. No model is involved in any decision made here.

A quoted span is accepted only if:
* it is a real quotation (not too short to be meaningful, not so long it quotes the whole page);
* it is found in the fetched source text (exact after normalization, or a near-exact alignment);
* every number in the span appears verbatim in the matched source window (45 can never match 54).

A claim is accepted against a found span only if:
* every number in the claim appears in the span (a claim cannot introduce a statistic);
* it does not flip the span's polarity (claim negated while span is not, or the reverse).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from rapidfuzz import fuzz

MIN_SPAN_CHARS = 40
MIN_SPAN_WORDS = 6
MAX_SPAN_CHARS = 700
MIN_SPAN_SCORE = 0.95  # partial-alignment similarity after normalization

_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "®": "", "™": "", "©": "",
})  # fmt: skip
_WS = re.compile(r"\s+")
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")

# Polarity words. "Strong" negations in a span that a positive claim would contradict.
_NEG_ANY = re.compile(
    r"\b(not|no|never|none|neither|nor|without|cannot|can't|didn't|doesn't|don't|isn't|wasn't|weren't"
    r"|fail(?:ed|s)?|insufficient|inconsistent|lack(?:ed|s)?)\b",
    re.IGNORECASE,
)
_NEG_STRONG = re.compile(
    r"\b(not|no evidence|never|cannot|did not|does not|do not|didn't|doesn't|failed to|insufficient evidence"
    r"|no\s+(?:[\w-]+\s+){0,2}(?:effects?|differences?|impacts?|changes?|improvements?|associations?|benefits?))\b",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    return _WS.sub(" ", text).strip().lower()


def numbers(text: str) -> Counter[str]:
    """Numeric tokens, normalized: '1,234' → '1234'; percent signs kept ('64%')."""
    return Counter(m.group(0).replace(",", "").rstrip(".") for m in _NUM.finditer(unicodedata.normalize("NFKC", text)))


@dataclass(frozen=True)
class SpanResult:
    ok: bool
    score: float
    reason: str | None = None  # machine-readable failure code
    matched: str | None = None


def check_span(span: str, source_text: str) -> SpanResult:
    n_span = normalize(span)
    if len(n_span) < MIN_SPAN_CHARS or len(n_span.split()) < MIN_SPAN_WORDS:
        return SpanResult(False, 0.0, "span_too_short")
    if len(n_span) > MAX_SPAN_CHARS:
        return SpanResult(False, 0.0, "span_too_long")
    n_doc = normalize(source_text)
    if not n_doc:
        return SpanResult(False, 0.0, "source_empty")
    if n_span in n_doc:
        return SpanResult(True, 1.0, None, n_span)
    alignment = fuzz.partial_ratio_alignment(n_span, n_doc)
    if alignment is None:
        return SpanResult(False, 0.0, "span_not_found")
    score = round(alignment.score / 100, 4)
    window = n_doc[alignment.dest_start : alignment.dest_end]
    if score < MIN_SPAN_SCORE:
        return SpanResult(False, score, "span_not_found")
    missing = numbers(n_span) - numbers(window)
    if missing:
        return SpanResult(False, score, "span_number_mismatch", window)
    return SpanResult(True, score, None, window)


def check_claim(claim: str, span: str) -> str | None:
    """Failure code if the claim is not supported by the (already found) span, else None."""
    extra = numbers(claim) - numbers(span)
    if extra:
        return "claim_numbers_not_in_span"
    claim_neg = bool(_NEG_ANY.search(claim))
    span_strong_neg = bool(_NEG_STRONG.search(span))
    span_any_neg = bool(_NEG_ANY.search(span))
    if claim_neg and not span_any_neg:
        return "negation_mismatch"
    if span_strong_neg and not claim_neg:
        return "negation_mismatch"
    return None


def text_numbers_supported(text: str, supports: list[str]) -> bool:
    """Every number in model-written card text must appear in one of its verified supporting spans."""
    available = Counter()
    for s in supports:
        available |= numbers(s)
    return not (numbers(text) - available)
