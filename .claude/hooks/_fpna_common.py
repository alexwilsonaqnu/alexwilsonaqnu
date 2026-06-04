"""Shared helpers for the FP&A provenance hooks (§7).

Kept dependency-free (stdlib only) so the hooks run anywhere Claude Code runs,
including headless crons, without an install step.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Iterable

LEDGER_DIR_ENV = "FPNA_LEDGER_DIR"
DEFAULT_LEDGER_DIR = ".fpna/ledger"
DISPLAY_TOLERANCE = 0.005  # 0.5% relative — mirrors src/ledger/fact.ts


# --------------------------------------------------------------------------- #
# hook I/O
# --------------------------------------------------------------------------- #
def read_hook_input() -> dict[str, Any]:
    """Claude Code delivers the hook payload as JSON on stdin."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def block(reason: str) -> None:
    """Block the action: exit 2 with the reason on stderr (fed back to model)."""
    print(reason, file=sys.stderr)
    sys.exit(2)


def allow() -> None:
    sys.exit(0)


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #
def ledger_dir(cwd: str | None = None) -> str:
    d = os.environ.get(LEDGER_DIR_ENV, DEFAULT_LEDGER_DIR)
    if not os.path.isabs(d) and cwd:
        d = os.path.join(cwd, d)
    return d


def ledger_path(session_id: str, cwd: str | None = None) -> str:
    return os.path.join(ledger_dir(cwd), f"{session_id}.jsonl")


def load_facts(session_id: str, cwd: str | None = None) -> list[dict[str, Any]]:
    path = ledger_path(session_id, cwd)
    if not os.path.exists(path):
        return []
    facts: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                facts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return facts


def append_fact(session_id: str, fact: dict[str, Any], cwd: str | None = None) -> None:
    path = ledger_path(session_id, cwd)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(fact, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# numeric parsing — shared by ledger-append and the stop-gate
# --------------------------------------------------------------------------- #
# Wrapped in a non-capturing group: the inner `|` must NOT leak into the
# alternation branches of TOKEN_RE below, or every branch collapses to "a bare
# number" and percent/currency/year detection breaks.
_NUM = r"(?:[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?)"

# A token in prose that looks like a stated figure: currency, percent, multiple,
# or a bare number. We deliberately keep currency/%/x markers AND bare numbers.
TOKEN_RE = re.compile(
    r"(?P<cur>[$£€]\s?" + _NUM + r"\s?(?:[KkMmBb]n?|bn|mm)?)"
    r"|(?P<pct>" + _NUM + r"\s?%)"
    r"|(?P<mult>" + _NUM + r"\s?[x×])"
    r"|(?P<bare>" + _NUM + r"(?:\s?(?:[KkMmBb]n?|bn|mm))?)"
)

ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# identifiers that embed digits but are not stated figures: FY26, Q3, H1, FY2026
IDENT_RE = re.compile(r"\b(?:FY|Q|H|CY|P)\s?\d{1,4}\b", re.IGNORECASE)


def to_number(raw: str) -> float:
    """Normalise a token like '$1,234.5K' / '12.3%' / '3.2x' to a float magnitude.

    Scale suffixes (K/M/B) are applied. Percent/multiple markers are stripped
    (the sign/scale stays); callers handle %↔fraction matching separately.
    """
    s = raw.strip()
    mult = 1.0
    low = s.lower()
    if low.endswith(("bn", "b")):
        mult = 1e9
    elif low.endswith("mm") or low.endswith("m"):
        mult = 1e6
    elif low.endswith("k"):
        mult = 1e3
    cleaned = re.sub(r"[^\d.\-+]", "", s)
    if cleaned in ("", "+", "-", "."):
        return float("nan")
    try:
        return float(cleaned) * mult
    except ValueError:
        return float("nan")


def fact_number(fact: dict[str, Any]) -> float:
    v = fact.get("value")
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = re.sub(r"[\s,$%x×£€]", "", v)
        try:
            return float(cleaned)
        except ValueError:
            return float("nan")
    return float("nan")


def numbers_equal(a: float, b: float, tol: float = DISPLAY_TOLERANCE) -> bool:
    if a != a or b != b:  # NaN
        return False
    if a == b:
        return True
    scale = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / scale <= tol


def extract_output_numbers(text: str) -> list[tuple[str, float]]:
    """Pull stated figures from drafted prose, skipping dates and period ids.

    Returns (raw_token, magnitude) pairs. Bare integers that are plausibly
    structural (years, small counts/quarters 0-12) are ignored to cut noise;
    every currency/percent/multiple token is always checked.
    """
    # mask things that are not figures so they don't get tokenised:
    # fact-reference placeholders (their requestIds contain digits), then dates
    # and period ids.
    masked = re.sub(r"\{\{\s*fact:[^}]*\}\}", " ", text)
    masked = ISO_DATE_RE.sub(" ", masked)
    masked = IDENT_RE.sub(" ", masked)

    found: list[tuple[str, float]] = []
    for m in TOKEN_RE.finditer(masked):
        raw = m.group(0)
        kind = m.lastgroup
        val = to_number(raw)
        if val != val:
            continue
        if kind == "bare":
            iv = abs(val)
            # ignore obvious non-figures: years and tiny counts/quarters
            if iv == int(iv) and (iv <= 12 or 1900 <= iv <= 2099):
                continue
        found.append((raw, val))
    return found


def find_session_id(payload: dict[str, Any]) -> str | None:
    sid = payload.get("session_id") or os.environ.get("FPNA_SESSION_ID")
    return sid


def iter_numbers(obj: Any) -> Iterable[tuple[list[str], float]]:
    """Walk a JSON-ish structure yielding (path, number) for every numeric leaf."""
    stack: list[tuple[list[str], Any]] = [([], obj)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, bool):
            continue
        if isinstance(node, (int, float)):
            yield path, float(node)
        elif isinstance(node, str):
            s = node.replace(",", "").strip()
            if re.fullmatch(r"[-+]?\d+(\.\d+)?", s):
                yield path, float(s)
        elif isinstance(node, dict):
            for k, v in node.items():
                stack.append((path + [str(k)], v))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append((path + [str(i)], v))
