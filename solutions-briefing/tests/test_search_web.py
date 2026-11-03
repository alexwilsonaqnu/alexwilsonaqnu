"""Reporter search UI and card pages."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from solbrief.db import session_scope
from solbrief.db.models import (
    CardEvidence,
    CardExpert,
    CardLimitation,
    CardStatus,
    EvidenceGrade,
    Expert,
    ExpertStance,
    ItemType,
    SolutionCard,
)

NOW = datetime(2026, 11, 3, tzinfo=UTC)


@pytest.fixture
def client():
    from solbrief.web import search
    from solbrief.web.app import create_app

    search._hits.clear()
    with TestClient(create_app()) as c:
        yield c


def _card(s, slug, title, status=CardStatus.verified, publishable=True, classes=("homelessness",)):
    c = SolutionCard(
        slug=slug,
        title=title,
        item_type=ItemType.solution,
        response_what="Subsidized permanent housing for people experiencing homelessness.",
        insight_mechanism="Housing comes first.",
        problem_class_ids=list(classes),
        status=status,
        publishable=publishable,
        evidence_grade=EvidenceGrade.meta_analysis_or_rct,
        last_verified_at=NOW if status == CardStatus.verified else None,
    )
    s.add(c)
    s.flush()
    return c


@pytest.fixture
def library():
    with session_scope() as s:
        hf = _card(s, "housing-first", "Housing First programs")
        s.add_all(
            [
                CardEvidence(
                    card_id=hf.id,
                    pillar="response",
                    claim="c",
                    source_url="https://cdc.gov/hf",
                    quoted_support_span="Housing First programs provide regular, subsidized housing",
                    verified=True,
                    span_match_score=1.0,
                    fetched_at=NOW,
                    source_publisher="CDC",
                ),
                CardEvidence(
                    card_id=hf.id,
                    pillar="evidence",
                    claim="Median 41% greater housing stability.",
                    source_url="https://cdc.gov/hf",
                    quoted_support_span="Median 41% greater housing stability",
                    verified=True,
                    span_match_score=1.0,
                    fetched_at=NOW,
                ),
                CardEvidence(
                    card_id=hf.id,
                    pillar="evidence",
                    claim="DROPPED invented claim",
                    source_url="https://x.org",
                    quoted_support_span="invented",
                    verified=False,
                    failure_reason="span_not_found",
                ),
                CardLimitation(
                    card_id=hf.id,
                    limitation="No study took place in a rural setting.",
                    source_url="https://cdc.gov/hf",
                    quoted_support_span="None ... rural settings.",
                    verified=True,
                    span_match_score=1.0,
                ),
            ]
        )
        ex = Expert(
            name="Maria Lopez",
            institution="Northwestern University",
            stance=ExpertStance.researcher,
            contact_path_url="https://www.northwestern.edu/people/lopez",
            source_url="https://x.edu",
            status="active",
        )
        moved = Expert(
            name="Old Contact",
            institution="Gone University",
            stance=ExpertStance.critic,
            contact_path_url="https://gone.edu/old",
            source_url="https://x.edu",
            status="moved",
        )
        s.add_all([ex, moved])
        s.flush()
        s.add_all(
            [
                CardExpert(card_id=hf.id, expert_id=ex.id, relation="evaluator_author"),
                CardExpert(card_id=hf.id, expert_id=moved.id, relation="critic"),
            ]
        )
        _card(s, "rapid-rehousing", "Rapid rehousing for homeless families", status=CardStatus.provisional)
        _card(s, "stale-shelter", "Stale homelessness shelter card", status=CardStatus.stale, publishable=False)
        _card(s, "evil", '<script>alert("x")</script> Homelessness card')
    yield


def test_home_page_has_search_form(client):
    r = client.get("/")
    assert r.status_code == 200 and 'name="q"' in r.text and "Illinois" in r.text
    assert "/static/htmx.min.js" in r.text and client.get("/static/htmx.min.js").status_code == 200


def test_county_options_for_state(client):
    r = client.get("/counties?state=17")
    assert "Cook County" in r.text and r.text.count("<option") > 100
    assert client.get("/counties?state=zz").text.count("<option") == 1


def test_search_shows_publishable_cards_with_labels(client, library):
    r = client.get("/search", params={"q": "homelessness is rising downtown", "state": "17"})
    assert r.status_code == 200
    assert "Housing First programs" in r.text and "Rapid rehousing" in r.text
    assert "Stale homelessness shelter" not in r.text
    assert "Provisional" in r.text and "Meta-analysis / RCT" in r.text
    assert "No study took place in a rural setting." in r.text  # key limitation
    assert "Maria Lopez" in r.text and "Old Contact" not in r.text  # moved experts never shown
    assert "ranked for places like Illinois" in r.text
    assert "<html" in r.text  # full page without HX header


def test_htmx_request_returns_partial(client, library):
    r = client.get("/search", params={"q": "homeless families"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "<html" not in r.text and "Rapid rehousing" in r.text


def test_text_is_escaped(client, library):
    r = client.get("/search", params={"q": "homelessness card"})
    assert "<script>alert" not in r.text and "&lt;script&gt;" in r.text


def test_short_query_and_no_results(client, library):
    assert "Describe the problem" in client.get("/search", params={"q": "a"}).text
    r = client.get("/search", params={"q": "volcanic ash on airport runways"})
    assert "No published solutions match yet" in r.text and "SciLine" in r.text


def test_rate_limit(client, library, monkeypatch):
    from solbrief.config import get_settings

    monkeypatch.setattr(get_settings(), "search_rate_limit_per_minute", 2)
    for _ in range(2):
        assert client.get("/search", params={"q": "homelessness"}).status_code == 200
    r = client.get("/search", params={"q": "homelessness"})
    assert r.status_code == 429 and "Too many searches" in r.text


def test_card_page_shows_only_verified_quotes(client, library):
    r = client.get("/cards/housing-first")
    assert r.status_code == 200
    assert "Median 41% greater housing stability" in r.text
    assert "DROPPED invented claim" not in r.text
    assert "https://www.northwestern.edu/people/lopez" in r.text


def test_provisional_card_page_is_labelled(client, library):
    r = client.get("/cards/rapid-rehousing")
    assert r.status_code == 200 and "fewer than two independent sources" in r.text


@pytest.mark.parametrize("slug", ["stale-shelter", "does-not-exist"])
def test_unpublished_or_unknown_cards_404(client, library, slug):
    assert client.get(f"/cards/{slug}").status_code == 404
