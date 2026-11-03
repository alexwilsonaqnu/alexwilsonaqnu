"""Verify one solution card. This is the only code that may set `verified`, `publishable`, grade or status.

Per supporting quote (response / insight / evidence pillars, and limitations):
  blocked source → fetch → span found (numbers exact) → claim consistent with span (numbers, polarity)
  → optional entailment check (LLM, when configured) → verified or dropped with a reason code.
Per card:
  * publishable ⇔ response, insight, ≥1 evidence claim and ≥1 limitation all have verified support, and
    numbers in the card's own prose appear in its verified quotes;
  * verified ⇔ publishable and ≥2 independent publishers among verified quotes;
  * otherwise provisional (shown only if publishable, always labelled), or stale if it was live before;
  * grade = strongest cap among verified evidence, never above the drafter's proposal.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select

from solbrief.db.models import (
    CardEvidence,
    CardExpert,
    CardLimitation,
    CardStatus,
    CardVersion,
    EvidenceGrade,
    Expert,
    SolutionCard,
)
from solbrief.verify.experts import check_expert
from solbrief.verify.fetch import Document, Fetcher
from solbrief.verify.sources import (
    effective_cap,
    is_blocked,
    publisher_id,
    registrable_domain,
    strongest,
    weaker,
)
from solbrief.verify.spans import check_claim, check_span, text_numbers_supported

log = logging.getLogger(__name__)

# Optional semantic check (claim entailed by span?). Deterministic checks always run first; this can only
# drop a claim, never rescue one.
EntailmentCheck = Callable[[str, str], bool]


@dataclass
class SupportOutcome:
    ok: bool
    reason: str | None
    score: float | None
    document: Document | None


@dataclass
class CardReport:
    checked_at: str
    supports: list[dict] = field(default_factory=list)
    limitations: list[dict] = field(default_factory=list)
    experts: list[dict] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    pillars: dict = field(default_factory=dict)
    grade: str | None = None
    status: str | None = None
    publishable: bool = False
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def check_support(
    claim: str, span: str | None, url: str, fetch: Fetcher, entails: EntailmentCheck | None = None
) -> SupportOutcome:
    if not span:
        return SupportOutcome(False, "no_support_span", None, None)
    if is_blocked(url):
        return SupportOutcome(False, "blocked_source", None, None)
    doc = fetch(url)
    if doc.status != 200:
        return SupportOutcome(False, "source_unreachable", None, doc)
    if not doc.text:
        return SupportOutcome(False, "source_unreadable", None, doc)
    found = check_span(span, doc.text)
    if not found.ok:
        return SupportOutcome(False, found.reason, found.score, doc)
    reason = check_claim(claim, span)
    if reason:
        return SupportOutcome(False, reason, found.score, doc)
    if entails is not None:
        try:
            if not entails(claim, span):
                return SupportOutcome(False, "not_entailed", found.score, doc)
        except Exception:  # an outage must not verify anything
            log.exception("entailment check failed")
            return SupportOutcome(False, "entailment_unavailable", found.score, doc)
    return SupportOutcome(True, None, found.score, doc)


def _snapshot(card: SolutionCard) -> dict:
    return {
        "title": card.title,
        "status": card.status.value,
        "publishable": card.publishable,
        "evidence_grade": card.evidence_grade.value if card.evidence_grade else None,
        "response_what": card.response_what,
        "insight_mechanism": card.insight_mechanism,
        "verification": card.verification,
    }


def verify_card(
    session,
    card: SolutionCard,
    fetch: Fetcher,
    entails: EntailmentCheck | None = None,
    proposed_grade: EvidenceGrade | None = None,
    now: datetime | None = None,
) -> CardReport:
    now = now or datetime.now(UTC)
    report = CardReport(checked_at=now.isoformat())
    was_live = card.publishable and card.status in (CardStatus.verified, CardStatus.provisional)

    if card.status == CardStatus.retracted:
        report.status, report.reasons = "retracted", ["retracted_cards_are_not_reverified"]
        return report

    evidence = session.scalars(select(CardEvidence).where(CardEvidence.card_id == card.id)).all()
    verified_supports: dict[str, list[str]] = {"response": [], "insight": [], "evidence": []}
    caps: list[EvidenceGrade] = []
    publishers: set[str] = set()
    for ev in evidence:
        out = check_support(ev.claim, ev.quoted_support_span, ev.source_url, fetch, entails)
        ev.source_domain = registrable_domain(ev.source_url)
        ev.source_publisher = publisher_id(ev.source_url)
        ev.verified = out.ok
        ev.failure_reason = out.reason
        ev.span_match_score = out.score if out.ok else None
        ev.fetched_at = out.document.fetched_at if out.document else None
        ev.content_sha256 = out.document.sha256 if out.document else None
        report.supports.append(
            {
                "id": ev.id,
                "pillar": ev.pillar,
                "url": ev.source_url,
                "ok": out.ok,
                "reason": out.reason,
                "score": out.score,
            }
        )
        if out.ok:
            verified_supports[ev.pillar].append(ev.quoted_support_span)
            publishers.add(ev.source_publisher)
            if ev.pillar == "evidence":
                caps.append(effective_cap(ev.source_type, ev.source_url, out.document.text))

    limitations = session.scalars(select(CardLimitation).where(CardLimitation.card_id == card.id)).all()
    verified_limitations = 0
    for lim in limitations:
        out = check_support(lim.limitation, lim.quoted_support_span, lim.source_url, fetch, entails)
        lim.verified = out.ok
        lim.failure_reason = out.reason
        lim.span_match_score = out.score if out.ok else None
        lim.fetched_at = out.document.fetched_at if out.document else None
        report.limitations.append({"id": lim.id, "url": lim.source_url, "ok": out.ok, "reason": out.reason})
        if out.ok:
            verified_limitations += 1
            publishers.add(publisher_id(lim.source_url))

    # Experts (affiliation)
    links = (
        session.execute(
            select(Expert).join(CardExpert, CardExpert.expert_id == Expert.id).where(CardExpert.card_id == card.id)
        )
        .scalars()
        .all()
    )
    for ex in links:
        res = check_expert(ex.name, ex.institution, ex.contact_path_url, fetch)
        if res.ok:
            ex.status, ex.last_verified_at = "active", now
        elif ex.status == "active":
            ex.status = "moved"  # was verified before; no longer on their institutional page
        report.experts.append({"id": str(ex.id), "ok": res.ok, "reason": res.reason, "status": ex.status})

    # Pillars: each needs verified support, and the card's own prose may not carry unsupported numbers.
    response_ok = bool(verified_supports["response"]) and text_numbers_supported(
        card.response_what or "", verified_supports["response"]
    )
    insight_text = f"{card.insight_mechanism or ''} {card.insight_transferability or ''}"
    insight_ok = (
        bool(card.insight_mechanism)
        and bool(verified_supports["insight"])
        and text_numbers_supported(insight_text, verified_supports["insight"])
    )
    report.pillars = {
        "response": response_ok,
        "insight": insight_ok,
        "evidence": bool(verified_supports["evidence"]),
        "limitations": verified_limitations > 0,
    }
    for pillar, ok in report.pillars.items():
        if not ok:
            report.reasons.append(f"pillar_unsupported:{pillar}")
    report.publishers = sorted(publishers)
    publishable = all(report.pillars.values())

    grade = strongest(caps)
    if grade is not None and proposed_grade is not None:
        grade = weaker(grade, proposed_grade)

    if publishable and len(publishers) >= 2:
        status = CardStatus.verified
    elif publishable:
        status = CardStatus.provisional
        report.reasons.append("fewer_than_two_independent_publishers")
    elif was_live:
        status = CardStatus.stale
        report.reasons.append("lost_verified_support")
    else:
        status = CardStatus.provisional

    if was_live and links and not any(e["status"] == "active" for e in report.experts):
        if any(e["status"] == "moved" for e in report.experts):
            status = CardStatus.stale
            publishable = False
            report.reasons.append("all_experts_moved")

    changed = (status, publishable) != (card.status, card.publishable)
    card.evidence_grade = grade
    card.status = status
    card.publishable = publishable
    if status == CardStatus.verified:
        card.last_verified_at = now
    report.grade = grade.value if grade else None
    report.status = status.value
    report.publishable = publishable
    card.verification = report.as_dict()
    if changed:
        card.version = (card.version or 1) + 1
        next_version = (
            session.scalar(select(func.max(CardVersion.version)).where(CardVersion.card_id == card.id)) or 0
        ) + 1
        session.add(
            CardVersion(
                card_id=card.id, version=next_version, snapshot=_snapshot(card), reason=f"verifier: {status.value}"
            )
        )
    return report
