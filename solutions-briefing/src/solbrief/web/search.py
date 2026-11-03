"""Reporter-facing search: problem statement (+ optional location) → ranked solution cards; card detail pages.

Uses the shared matcher. Only publishable cards are ever shown; provisional cards are always labelled; experts
are shown only when the verifier has confirmed their institutional page (otherwise SciLine is offered).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from solbrief.classify import anthropic_classifier
from solbrief.config import get_settings
from solbrief.db import session_scope
from solbrief.db.models import (
    CardEvidence,
    CardExpert,
    CardLimitation,
    CardStatus,
    Expert,
    PriorCoverage,
    SolutionCard,
)
from solbrief.embed import voyage_embedder
from solbrief.geo import STATES, counties, describe, normalize_fips
from solbrief.matcher import MatchQuery, match
from solbrief.taxonomy import load_snapshot
from solbrief.verify.sources import publisher_id

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()

SCILINE = "https://www.sciline.org/services/expert-matching/"
GRADE_LABELS = {
    "meta_analysis_or_rct": "Meta-analysis / RCT",
    "quasi_experimental": "Quasi-experimental",
    "observational_or_admin_data": "Observational / admin data",
    "self_reported": "Self-reported",
    "anecdotal": "Anecdotal",
}
templates.env.globals.update(GRADE_LABELS=GRADE_LABELS, SCILINE=SCILINE)
templates.env.filters["publisher"] = publisher_id


@lru_cache
def _embedder():
    return voyage_embedder()


@lru_cache
def _classifier():
    return anthropic_classifier()


@lru_cache
def _class_labels() -> dict[str, str]:
    return {c.id: c.label for c in load_snapshot("v1")[1]}


# --------------------------------------------------------------------------- rate limiting (per IP, per process)

_hits: dict[str, deque] = defaultdict(deque)


def _rate_limited(request: Request) -> bool:
    limit = get_settings().search_rate_limit_per_minute
    ip = request.headers.get("fly-client-ip") or (request.client.host if request.client else "unknown")
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


# --------------------------------------------------------------------------- views


def _states() -> list[tuple[str, str]]:
    return sorted(((f, name) for f, (_, name) in STATES.items()), key=lambda x: x[1])


def _card_summary(s, card: SolutionCard) -> dict:
    limitation = s.scalar(
        select(CardLimitation.limitation)
        .where(CardLimitation.card_id == card.id, CardLimitation.verified.is_(True))
        .order_by(CardLimitation.id)
        .limit(1)
    )
    experts = _active_experts(s, card)
    return {
        "card": card,
        "limitation": limitation,
        "experts": experts[:2],
        "classes": [_class_labels().get(c, c) for c in card.problem_class_ids],
        "places": [describe(f) for f in card.response_where_fips[:3]],
    }


def _active_experts(s, card: SolutionCard) -> list[dict]:
    rows = s.execute(
        select(Expert, CardExpert.relation)
        .join(CardExpert, CardExpert.expert_id == Expert.id)
        .where(CardExpert.card_id == card.id, Expert.status == "active")
        .order_by(Expert.name)
    ).all()
    return [{"expert": e, "relation": rel} for e, rel in rows]


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "search.html", {"states": _states(), "q": "", "results": None})


@router.get("/counties", response_class=HTMLResponse)
def county_options(request: Request, state: str = ""):
    options = (
        sorted(
            ((c.fips, c.name) for c in counties().values() if c.fips[:2] == state and not c.legacy), key=lambda x: x[1]
        )
        if state in STATES
        else []
    )
    return templates.TemplateResponse(request, "_county_options.html", {"counties": options})


@router.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = Query("", max_length=500), state: str = "", county: str = ""):
    q = q.strip()
    partial = request.headers.get("hx-request") == "true"
    ctx = {"states": _states(), "q": q, "state": state, "county": county}
    if len(q) < 3:
        ctx.update(results=None, error="Describe the problem in a few words.")
        return templates.TemplateResponse(request, "_results.html" if partial else "search.html", ctx)
    if _rate_limited(request):
        return templates.TemplateResponse(
            request,
            "_results.html" if partial else "search.html",
            {**ctx, "results": None, "error": "Too many searches in a minute; please wait a moment."},
            status_code=429,
        )
    fips = [f for f in (normalize_fips(county or state or ""),) if f]
    with session_scope() as s:
        outcome = match(s, MatchQuery(text=q, fips=fips, k=8), embedder=_embedder(), classifier=_classifier())
        cards = {
            c.id: c
            for c in s.scalars(select(SolutionCard).where(SolutionCard.id.in_([r.card_id for r in outcome.cards])))
        }
        results = [{**_card_summary(s, cards[r.card_id]), "match": r} for r in outcome.cards if r.card_id in cards]
        ctx.update(
            results=results,
            classes=[_class_labels().get(c, c) for c in outcome.classes],
            place=describe(fips[0]) if fips else None,
            semantic=outcome.signals.get("semantic", False),
        )
        return templates.TemplateResponse(request, "_results.html" if partial else "search.html", ctx)


@router.get("/cards/{slug}", response_class=HTMLResponse)
def card_detail(request: Request, slug: str):
    with session_scope() as s:
        card = s.scalar(select(SolutionCard).where(SolutionCard.slug == slug))
        if card is None or not card.publishable or card.status not in (CardStatus.verified, CardStatus.provisional):
            raise HTTPException(404, "No published card with that address.")
        quotes = s.scalars(
            select(CardEvidence)
            .where(CardEvidence.card_id == card.id, CardEvidence.verified.is_(True))
            .order_by(CardEvidence.pillar, CardEvidence.id)
        ).all()
        limitations = s.scalars(
            select(CardLimitation)
            .where(CardLimitation.card_id == card.id, CardLimitation.verified.is_(True))
            .order_by(CardLimitation.id)
        ).all()
        coverage = s.scalars(
            select(PriorCoverage).where(PriorCoverage.card_id == card.id).order_by(PriorCoverage.published_at.desc())
        ).all()
        by_pillar = {p: [q for q in quotes if q.pillar == p] for p in ("response", "insight", "evidence")}
        return templates.TemplateResponse(
            request,
            "card.html",
            {
                "card": card,
                "quotes": by_pillar,
                "limitations": limitations,
                "coverage": coverage,
                "experts": _active_experts(s, card),
                "classes": [_class_labels().get(c, c) for c in card.problem_class_ids],
                "places": [describe(f) for f in card.response_where_fips],
            },
        )
