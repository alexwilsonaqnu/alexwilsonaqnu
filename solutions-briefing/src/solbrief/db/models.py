"""SQLAlchemy models — the schema from docs/PLAN.md §3.

Design rules encoded here (not just in prompts):
* Expert contact paths must be https institutional URLs — never mailto:/tel: (CHECK constraint).
* Problem classes are never renamed: `id` is a stable slug; label changes go through taxonomy_changes.
* Idempotency keys: problems(cluster_id, run_date), runs(run_date, stage, dry_run),
  briefings(reader_id, date, kind).
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1024  # voyage-3.5 default output dimension


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _pg_enum(py_enum: type[enum.Enum], name: str) -> Enum:
    return Enum(py_enum, name=name, values_callable=lambda e: [m.value for m in e])


class ItemType(enum.StrEnum):
    solution = "solution"
    service = "service"


class CardStatus(enum.StrEnum):
    provisional = "provisional"
    verified = "verified"
    stale = "stale"
    retracted = "retracted"


class EvidenceGrade(enum.StrEnum):
    meta_analysis_or_rct = "meta_analysis_or_rct"
    quasi_experimental = "quasi_experimental"
    observational_or_admin_data = "observational_or_admin_data"
    self_reported = "self_reported"
    anecdotal = "anecdotal"


class ExpertStance(enum.StrEnum):
    researcher = "researcher"
    practitioner = "practitioner"
    critic = "critic"


class FeedbackSignal(enum.StrEnum):
    used = "used"
    bad_lead = "bad_lead"
    already_covered = "already_covered"


class RunStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    partial = "partial"
    failed = "failed"
    skipped = "skipped"


# --------------------------------------------------------------------------- taxonomy


class TaxonomyVersion(Base):
    __tablename__ = "taxonomy_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class ProblemClass(Base):
    __tablename__ = "problem_classes"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # stable slug, never renamed
    label: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("problem_classes.id"))
    description: Mapped[str | None] = mapped_column(Text)
    external_tag: Mapped[str | None] = mapped_column(Text)  # e.g. SJN issue area, once licensed
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    introduced_in_version: Mapped[int | None] = mapped_column(ForeignKey("taxonomy_versions.id"))
    deprecated_in_version: Mapped[int | None] = mapped_column(ForeignKey("taxonomy_versions.id"))
    __table_args__ = (CheckConstraint("status IN ('active','proposed','deprecated')", name="problem_class_status"),)


class TaxonomyChange(Base):
    """Append-only log. Agents may only insert change_type='propose'."""

    __tablename__ = "taxonomy_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("taxonomy_versions.id"))
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    class_id: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(Text, nullable=False)  # 'agent' | 'human:<email>'
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('propose','accept','reject','deprecate','relabel_display')",
            name="taxonomy_change_type",
        ),
        CheckConstraint("actor LIKE 'human:%' OR change_type = 'propose'", name="taxonomy_agents_only_propose"),
    )


# --------------------------------------------------------------------------- problems


class Problem(Base):
    __tablename__ = "problems"
    id: Mapped[uuid.UUID] = _uuid_pk()
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    cluster_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    problem_class_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    fips: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    is_national: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    source_adapter: Mapped[str] = mapped_column(Text, nullable=False)
    source_urls: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[float | None] = mapped_column(Float)
    novelty: Mapped[float | None] = mapped_column(Float)
    locality_score: Mapped[float | None] = mapped_column(Float)
    item_type: Mapped[ItemType] = mapped_column(_pg_enum(ItemType, "item_type"), nullable=False)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("cluster_id", "run_date", name="uq_problem_cluster_day"),
        Index("ix_problems_classes", "problem_class_ids", postgresql_using="gin"),
        Index("ix_problems_fips", "fips", postgresql_using="gin"),
        Index("ix_problems_run_date", "run_date"),
        Index(
            "ix_problems_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# --------------------------------------------------------------------------- solution library


class SolutionCard(Base):
    __tablename__ = "solution_cards"
    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    item_type: Mapped[ItemType] = mapped_column(_pg_enum(ItemType, "item_type"), nullable=False)
    # Pillar 1: response
    response_what: Mapped[str] = mapped_column(Text, nullable=False)
    response_who: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    response_where_fips: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    response_started_at: Mapped[date | None] = mapped_column(Date)
    # Pillar 2: insight
    insight_mechanism: Mapped[str | None] = mapped_column(Text)
    insight_transferability: Mapped[str | None] = mapped_column(Text)
    # Pillar 3: evidence (rows in card_evidence) + grade
    evidence_grade: Mapped[EvidenceGrade | None] = mapped_column(_pg_enum(EvidenceGrade, "evidence_grade"))
    # Pillar 4: limitations (rows in card_limitations)
    problem_class_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    context_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    status: Mapped[CardStatus] = mapped_column(
        _pg_enum(CardStatus, "card_status"), nullable=False, server_default=CardStatus.provisional.value
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    drafted_by_run: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    verified_by_run: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = _now()
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        # A card can only be 'verified' if it has a verification timestamp and a grade.
        CheckConstraint(
            "status <> 'verified' OR (last_verified_at IS NOT NULL AND evidence_grade IS NOT NULL)",
            name="verified_requires_verification",
        ),
        Index("ix_cards_classes", "problem_class_ids", postgresql_using="gin"),
        Index("ix_cards_status", "status"),
        Index(
            "ix_cards_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class CardEvidence(Base):
    __tablename__ = "card_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("solution_cards.id", ondelete="CASCADE"))
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_publisher: Mapped[str | None] = mapped_column(Text)
    source_domain: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(Text)
    quoted_support_span: Mapped[str] = mapped_column(Text, nullable=False)
    span_match_score: Mapped[float | None] = mapped_column(Float)
    content_sha256: Mapped[str | None] = mapped_column(Text)
    archived_url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    __table_args__ = (
        CheckConstraint(
            "NOT verified OR (span_match_score IS NOT NULL AND fetched_at IS NOT NULL)",
            name="evidence_verified_requires_match",
        ),
        Index("ix_evidence_card", "card_id"),
    )


class CardLimitation(Base):
    __tablename__ = "card_limitations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("solution_cards.id", ondelete="CASCADE"))
    limitation: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    quoted_support_span: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    __table_args__ = (Index("ix_limitations_card", "card_id"),)


class CardVersion(Base):
    __tablename__ = "card_versions"
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("solution_cards.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------- experts


class Expert(Base):
    __tablename__ = "experts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text)
    institution: Mapped[str | None] = mapped_column(Text)
    expertise_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    stance: Mapped[ExpertStance] = mapped_column(_pg_enum(ExpertStance, "expert_stance"), nullable=False)
    contact_path_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    openalex_id: Mapped[str | None] = mapped_column(Text)
    orcid: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="unverified")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        # Institutional pages / media-relations offices only. Never personal contact details.
        CheckConstraint(
            "contact_path_url ~* '^https://' AND contact_path_url !~* '(mailto:|tel:|@)'",
            name="expert_contact_is_institutional_url",
        ),
        CheckConstraint("status IN ('active','moved','unverified')", name="expert_status"),
        Index("ix_experts_tags", "expertise_tags", postgresql_using="gin"),
    )


class CardExpert(Base):
    __tablename__ = "card_experts"
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("solution_cards.id", ondelete="CASCADE"), primary_key=True)
    expert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experts.id", ondelete="CASCADE"), primary_key=True)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("relation IN ('evaluator_author','practitioner','critic')", name="card_expert_relation"),
    )


class PriorCoverage(Base):
    __tablename__ = "prior_coverage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("solution_cards.id", ondelete="CASCADE"))
    outlet: Mapped[str] = mapped_column(Text, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("card_id", "url", name="uq_prior_coverage_card_url"),)


# --------------------------------------------------------------------------- readers & delivery


class Reader(Base):
    __tablename__ = "readers"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    beats: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    coverage_fips: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    is_national: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    cadence: Mapped[str] = mapped_column(Text, nullable=False, server_default="daily")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="America/Chicago")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    created_at: Mapped[datetime] = _now()
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("status IN ('pending','active','unsubscribed','bounced')", name="reader_status"),)


class Briefing(Base):
    __tablename__ = "briefings"
    id: Mapped[uuid.UUID] = _uuid_pk()
    reader_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("readers.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="daily")
    items: Mapped[list | None] = mapped_column(JSONB)
    rendered_html: Mapped[str | None] = mapped_column(Text)
    rendered_text: Mapped[str | None] = mapped_column(Text)
    evaluator_log: Mapped[list | None] = mapped_column(JSONB)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("reader_id", "date", "kind", name="uq_briefing_reader_day_kind"),
        CheckConstraint("kind IN ('daily','reply')", name="briefing_kind"),
        # A dry-run briefing can never be marked sent.
        CheckConstraint("NOT (dry_run AND sent_at IS NOT NULL)", name="dry_run_never_sent"),
    )


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reader_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("readers.id", ondelete="CASCADE"))
    briefing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("briefings.id", ondelete="SET NULL"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("solution_cards.id", ondelete="CASCADE"))
    signal: Mapped[FeedbackSignal] = mapped_column(_pg_enum(FeedbackSignal, "feedback_signal"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class InboundQuery(Base):
    __tablename__ = "inbound_queries"
    id: Mapped[uuid.UUID] = _uuid_pk()
    reader_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("readers.id", ondelete="SET NULL"))
    provider_message_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    stripped_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="received")
    briefing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("briefings.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------- operations


class Run(Base):
    """One row per pipeline stage per day (per dry-run flag)."""

    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[RunStatus] = mapped_column(_pg_enum(RunStatus, "run_status"), nullable=False)
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    trace_id: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("run_date", "stage", "dry_run", name="uq_run_day_stage"),)


class SourceFetch(Base):
    """Cache of fetched source documents; shared by adapters and the verifier."""

    __tablename__ = "source_fetch_cache"
    url: Mapped[str] = mapped_column(Text, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
