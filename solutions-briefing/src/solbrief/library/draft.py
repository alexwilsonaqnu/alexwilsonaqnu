"""The card draft contract (what the drafter must return) and how a draft is stored as a provisional card.

Drafts are untrusted: they are stored with every quote unverified and `verification = NULL`, so nothing a
drafter writes can reach a reader until the verifier (separate context, deterministic checks) has run.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from solbrief.db.models import (
    CardEvidence,
    CardExpert,
    CardLimitation,
    EvidenceGrade,
    Expert,
    ExpertStance,
    ItemType,
    SolutionCard,
)
from solbrief.geo import normalize_fips
from solbrief.taxonomy import class_ids
from solbrief.verify.sources import SOURCE_TYPES

_EMAIL_OR_PHONE = re.compile(r"@|mailto:|tel:|\+?\d[\d\s().-]{8,}\d")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quote(_Strict):
    claim: str = Field(description="One sentence. Every number in it must appear in `span`.")
    source_url: str = Field(description="The exact URL you fetched with fetch_source.")
    span: str = Field(description="Verbatim text copied from that page (40-700 characters).")


class EvidenceQuote(Quote):
    source_type: Literal[SOURCE_TYPES] = Field(description="Honest type of this source (see evidence-grading).")  # type: ignore[valid-type]


class LimitationQuote(_Strict):
    limitation: str
    source_url: str
    span: str


class ExpertDraft(_Strict):
    name: str
    role: str | None = None
    institution: str
    stance: Literal["researcher", "practitioner", "critic"]
    contact_path_url: str = Field(description="https institutional page that names this person.")
    source_url: str = Field(description="Where you found this person in connection with the solution.")

    @field_validator("contact_path_url")
    @classmethod
    def institutional_only(cls, v: str) -> str:
        if not v.lower().startswith("https://") or _EMAIL_OR_PHONE.search(v.split("//", 1)[1]):
            raise ValueError("contact_path_url must be an https institutional page, never an email or phone")
        return v


class CardDraft(_Strict):
    title: str = Field(max_length=120)
    item_type: Literal["solution", "service"] = "solution"
    response_what: str = Field(description="One sentence: what is done, by whom, where, since when.")
    response_who: list[str] = Field(default_factory=list)
    response_where_fips: list[str] = Field(default_factory=list, description="State or county FIPS codes.")
    response_started_at: date | None = None
    insight_mechanism: str
    insight_transferability: str
    response_quotes: list[Quote] = Field(min_length=1)
    insight_quotes: list[Quote] = Field(min_length=1)
    evidence: list[EvidenceQuote] = Field(min_length=1)
    limitations: list[LimitationQuote] = Field(min_length=1)
    problem_class_ids: list[str] = Field(min_length=1)
    context_tags: list[str] = Field(default_factory=list)
    experts: list[ExpertDraft] = Field(default_factory=list)
    proposed_grade: Literal[tuple(g.value for g in EvidenceGrade)]  # type: ignore[valid-type]

    @field_validator("problem_class_ids")
    @classmethod
    def known_classes(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in class_ids()]
        if unknown:
            raise ValueError(f"unknown taxonomy classes {unknown}; propose new classes separately")
        return v


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] + "-" + uuid.uuid4().hex[:6]


def store_draft(session, draft: CardDraft, run_id: uuid.UUID | None = None) -> SolutionCard:
    """Persist a draft as a provisional, unverified card. Returns the card (flushed, with id)."""
    card = SolutionCard(
        slug=_slug(draft.title),
        title=draft.title,
        item_type=ItemType(draft.item_type),
        response_what=draft.response_what,
        response_who=draft.response_who,
        response_where_fips=[f for f in (normalize_fips(x) for x in draft.response_where_fips) if f],
        response_started_at=draft.response_started_at,
        insight_mechanism=draft.insight_mechanism,
        insight_transferability=draft.insight_transferability,
        problem_class_ids=draft.problem_class_ids,
        context_tags=draft.context_tags,
        drafted_by_run=run_id,
        publishable=False,
    )
    session.add(card)
    session.flush()
    for pillar, quotes in (("response", draft.response_quotes), ("insight", draft.insight_quotes)):
        for q in quotes:
            session.add(
                CardEvidence(
                    card_id=card.id, pillar=pillar, claim=q.claim, source_url=q.source_url, quoted_support_span=q.span
                )
            )
    for q in draft.evidence:
        session.add(
            CardEvidence(
                card_id=card.id,
                pillar="evidence",
                claim=q.claim,
                source_url=q.source_url,
                quoted_support_span=q.span,
                source_type=q.source_type,
            )
        )
    for lim in draft.limitations:
        session.add(
            CardLimitation(
                card_id=card.id, limitation=lim.limitation, source_url=lim.source_url, quoted_support_span=lim.span
            )
        )
    for ex in draft.experts:
        expert = session.scalar(select(Expert).where(Expert.name == ex.name, Expert.institution == ex.institution))
        if expert is None:
            expert = Expert(
                name=ex.name,
                role=ex.role,
                institution=ex.institution,
                stance=ExpertStance(ex.stance),
                contact_path_url=ex.contact_path_url,
                source_url=ex.source_url,
                status="unverified",
            )
            session.add(expert)
            session.flush()
        relation = {"researcher": "evaluator_author", "practitioner": "practitioner", "critic": "critic"}[ex.stance]
        session.merge(CardExpert(card_id=card.id, expert_id=expert.id, relation=relation, source_url=ex.source_url))
    session.flush()
    return card
