"""Matcher, embeddings and the match stage, on a small hand-built library with a deterministic fake embedder."""

import hashlib
import math
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from solbrief.db import session_scope
from solbrief.db.models import (
    CardStatus,
    EvidenceGrade,
    Feedback,
    FeedbackSignal,
    ItemType,
    MatchResult,
    Problem,
    Reader,
    SolutionCard,
)
from solbrief.embed import embed_pending
from solbrief.matcher import MatchQuery, class_score, match, recall_at_k
from solbrief.pipeline.match_stage import in_coverage, match_stage
from solbrief.pipeline.stages import StageContext

NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
D = NOW.date()


def fake_embedder(texts, input_type):
    """Bag-of-words hashed into 1024 dims, L2-normalized: similar wording → similar vectors."""
    out = []
    for t in texts:
        v = [0.0] * 1024
        for w in t.lower().split():
            w = "".join(ch for ch in w if ch.isalnum())
            if len(w) > 2:
                v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % 1024] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def card(
    s,
    slug,
    title,
    response,
    classes,
    *,
    status=CardStatus.verified,
    publishable=True,
    where=(),
    grade=EvidenceGrade.meta_analysis_or_rct,
    verified_days_ago=10,
):
    c = SolutionCard(
        slug=slug,
        title=title,
        item_type=ItemType.solution,
        response_what=response,
        insight_mechanism=f"Mechanism of {title}.",
        problem_class_ids=list(classes),
        status=status,
        publishable=publishable,
        response_where_fips=list(where),
        evidence_grade=grade,
        last_verified_at=NOW - timedelta(days=verified_days_ago) if status == CardStatus.verified else None,
    )
    s.add(c)
    s.flush()
    return c


@pytest.fixture
def library():
    with session_scope() as s:
        card(
            s,
            "housing-first",
            "Housing First programs",
            "Subsidized permanent housing for people experiencing homelessness without sobriety requirements.",
            ["homelessness"],
            where=["17031"],
        )
        card(
            s,
            "eviction-diversion",
            "Eviction diversion programs",
            "Court-based mediation and emergency rental assistance to keep tenants housed.",
            ["eviction"],
            status=CardStatus.provisional,
            where=["30063"],
            grade=EvidenceGrade.quasi_experimental,
        )
        card(
            s,
            "digital-vaping",
            "Digital interventions to prevent teen substance use",
            "Apps and text-message programs that reduce adolescent vaping and binge drinking.",
            ["overdose-substance-use", "youth-mental-health"],
        )
        card(
            s,
            "stale-shelter",
            "Old shelter program",
            "Shelter beds for people experiencing homelessness.",
            ["homelessness"],
            status=CardStatus.stale,
            publishable=False,
        )
        card(
            s,
            "unverified-draft",
            "Unverified homelessness draft",
            "Homelessness program draft.",
            ["homelessness"],
            status=CardStatus.provisional,
            publishable=False,
        )
        card(
            s,
            "retracted-one",
            "Retracted homelessness card",
            "Homelessness program.",
            ["homelessness"],
            status=CardStatus.retracted,
            publishable=False,
        )
    embed_pending(embedder=fake_embedder)
    yield


def run(q, **kw):
    with session_scope() as s:
        return match(s, q, now=NOW, **kw)


def slugs(outcome):
    return [c.slug for c in outcome.cards]


# --------------------------------------------------------------------------- retrieval & safety


def test_only_publishable_live_cards_are_ever_returned(library):
    out = run(MatchQuery(text="homelessness shelter housing program", k=10), embedder=fake_embedder)
    assert "housing-first" in slugs(out)
    assert not {"stale-shelter", "unverified-draft", "retracted-one"} & set(slugs(out))


def test_class_overlap_parent_child_partial_credit():
    assert class_score(["homelessness"], ["homelessness"]) == 1.0
    assert class_score(["homelessness"], ["housing"]) == 0.5  # parent
    assert class_score(["eviction"], ["homelessness"]) == 0.0  # sibling, not related
    assert class_score(["eviction"], []) == 0.0


def test_lexical_retrieval_works_without_classes_or_embeddings(library):
    out = run(MatchQuery(text="rental assistance mediation for tenants", class_ids=[]))
    assert slugs(out)[0] == "eviction-diversion"
    assert out.signals == {"semantic": False, "classes": False, "lexical": True}


def test_semantic_retrieval_and_features_are_explained(library):
    out = run(MatchQuery(text="teens vaping apps text message programs", class_ids=[]), embedder=fake_embedder)
    top = out.cards[0]
    assert top.slug == "digital-vaping"
    assert all(top.features["semantic"] > c.features["semantic"] for c in out.cards[1:])
    assert set(top.features) == {"semantic", "class", "lexical", "geo", "grade", "verified", "recency", "feedback"}


def test_typed_query_is_classified_by_rules(library):
    out = run(MatchQuery(text="County sees spike in fentanyl overdose deaths"))
    assert "overdose-substance-use" in out.classes and slugs(out)[0] == "digital-vaping"


def test_similar_sized_place_ranks_higher():
    with session_scope() as s:
        for slug, where in (("big-city", ["06037"]), ("small-town", ["30063"])):
            card(
                s,
                slug,
                "Housing First programs",
                "Permanent supportive housing for homeless residents.",
                ["homelessness"],
                where=where,
            )
    big = run(MatchQuery(text="homeless encampments", fips=["17031"]))  # Cook County (large)
    small = run(MatchQuery(text="homeless encampments", fips=["16055"]))  # Kootenai County, ID (small)
    assert slugs(big)[0] == "big-city" and slugs(small)[0] == "small-town"


def test_feedback_penalty_demotes_card(library):
    query = MatchQuery(text="housing for homeless people and tenants facing eviction", class_ids=["housing"], k=3)
    before = {c.slug: c for c in run(query).cards}
    assert before["housing-first"].features["feedback"] == 0.0
    with session_scope() as s:
        r = Reader(email="r@example.org", status="active")
        s.add(r)
        s.flush()
        hf = s.scalar(select(SolutionCard.id).where(SolutionCard.slug == "housing-first"))
        for _ in range(3):
            s.add(Feedback(reader_id=r.id, card_id=hf, signal=FeedbackSignal.bad_lead))
    after = {c.slug: c for c in run(query).cards}
    assert after["housing-first"].features["feedback"] == 1.0
    assert after["housing-first"].score == pytest.approx(before["housing-first"].score - 0.20)
    assert after["eviction-diversion"].score == pytest.approx(before["eviction-diversion"].score)


def test_ordering_is_deterministic(library):
    a = run(MatchQuery(text="housing program", k=10), embedder=fake_embedder)
    b = run(MatchQuery(text="housing program", k=10), embedder=fake_embedder)
    assert slugs(a) == slugs(b)


def test_service_problems_never_get_solution_cards(library):
    with session_scope() as s:
        p = Problem(
            run_date=D,
            cluster_id=uuid.uuid4(),
            title="Flood Warning — Chicago",
            source_adapter="nws",
            first_seen=NOW,
            last_seen=NOW,
            problem_class_ids=["flooding"],
            item_type=ItemType.service,
        )
        s.add(p)
        s.flush()
        pid = p.id
    out = run(MatchQuery(problem_id=pid))
    assert out.cards == [] and out.item_type == "service"


def test_problem_query_uses_stored_classes_and_places(library):
    with session_scope() as s:
        p = Problem(
            run_date=D,
            cluster_id=uuid.uuid4(),
            title="Rising homelessness in Cook County",
            summary="Shelter demand up",
            source_adapter="cdc_mmwr",
            first_seen=NOW,
            last_seen=NOW,
            problem_class_ids=["homelessness"],
            fips=["17031"],
            item_type=ItemType.solution,
        )
        s.add(p)
        s.flush()
        pid = p.id
    out = run(MatchQuery(problem_id=pid), embedder=fake_embedder)
    assert out.classes == ["homelessness"] and slugs(out)[0] == "housing-first"
    assert out.cards[0].features["geo"] == 1.0


def test_recall_at_k():
    assert recall_at_k([["a", "b", "c"], ["x"]], [["a", "z"], ["x"]], k=2) == 0.75
    assert math.isnan(recall_at_k([["a"]], [[]]))


# --------------------------------------------------------------------------- embeddings


def test_embed_pending_only_publishable_and_records_model(library):
    with session_scope() as s:
        rows = {c.slug: c for c in s.scalars(select(SolutionCard))}
        assert rows["housing-first"].embedding is not None and rows["housing-first"].embedding_model == "voyage-3.5"
        assert rows["unverified-draft"].embedding is None
    assert embed_pending(embedder=fake_embedder) == {"cards": 0, "problems": 0}  # nothing pending


def test_embed_pending_without_key_is_reported_not_failed():
    assert embed_pending()["reason"] == "VOYAGE_API_KEY not set"


# --------------------------------------------------------------------------- match stage


def _problem(s, title, classes, fips=(), national=False, severity=0.5, item_type=ItemType.solution):
    p = Problem(
        run_date=D,
        cluster_id=uuid.uuid4(),
        title=title,
        source_adapter="test",
        first_seen=NOW,
        last_seen=NOW,
        problem_class_ids=list(classes),
        fips=list(fips),
        is_national=national,
        severity=severity,
        novelty=1.0,
        locality_score=0.5,
        item_type=item_type,
    )
    s.add(p)
    return p


def test_coverage_rules():
    p_county = Problem(fips=["17031"], is_national=False, severity=0.3)
    p_state = Problem(fips=["17"], is_national=False, severity=0.3)
    p_far = Problem(fips=["06037"], is_national=False, severity=0.3)
    p_major = Problem(fips=["06037"], is_national=False, severity=1.0)
    p_nat = Problem(fips=[], is_national=True, severity=0.3)
    county_reader = Reader(coverage_fips=["17031"], is_national=False)
    state_reader = Reader(coverage_fips=["17"], is_national=False)
    us_reader = Reader(coverage_fips=[], is_national=True)
    assert in_coverage(p_county, county_reader) and in_coverage(p_state, county_reader)
    assert not in_coverage(p_far, county_reader) and in_coverage(p_nat, county_reader)
    assert in_coverage(p_county, state_reader) and not in_coverage(p_far, state_reader)
    assert in_coverage(p_nat, us_reader) and in_coverage(p_major, us_reader) and not in_coverage(p_far, us_reader)


def test_match_stage_writes_results_per_reader_idempotently(library):
    with session_scope() as s:
        _problem(s, "Homeless encampments grow in Chicago", ["homelessness"], fips=["17031"], severity=0.6)
        _problem(s, "Teen vaping survey results", ["overdose-substance-use"], national=True)
        _problem(s, "Flood Warning — Chicago", ["flooding"], fips=["17031"], item_type=ItemType.service)
        _problem(s, "Drought in Arizona", ["drought"], fips=["04013"])
        s.add(Reader(email="housing@example.org", status="active", beats=["housing"], coverage_fips=["17"]))
        s.add(Reader(email="all@example.org", status="active", beats=[], coverage_fips=["17031"]))
        s.add(Reader(email="paused@example.org", status="unsubscribed", beats=[], coverage_fips=["17031"]))
    first = match_stage(StageContext(run_date=D, dry_run=True))
    assert first.counts["readers"] == 2
    with session_scope() as s:
        rows = s.execute(
            select(Reader.email, Problem.title, SolutionCard.slug)
            .join(MatchResult, MatchResult.reader_id == Reader.id)
            .join(Problem, Problem.id == MatchResult.problem_id)
            .outerjoin(SolutionCard, SolutionCard.id == MatchResult.card_id)
        ).all()
    housing = {(t, c) for e, t, c in rows if e == "housing@example.org"}
    assert housing == {("Homeless encampments grow in Chicago", "housing-first")}  # beat filter
    general = {t for e, t, c in rows if e == "all@example.org"}
    assert "Drought in Arizona" not in general  # outside coverage
    assert ("Flood Warning — Chicago", None) in {(t, c) for e, t, c in rows if e == "all@example.org"}
    total = len(rows)
    match_stage(StageContext(run_date=D, dry_run=True))
    with session_scope() as s:
        assert len(s.scalars(select(MatchResult.id)).all()) == total  # re-run rewrites, no duplicates


def test_match_stage_with_no_readers():
    assert match_stage(StageContext(run_date=date(2026, 11, 4), dry_run=True)).counts["readers"] == 0
