"""Verifier unit tests: spans, numbers, grading caps, publishers, URL safety, experts, fetch cache, staleness."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

import solbrief.verify.fetch as fetch_mod
from solbrief.adapters.base import PoliteClient
from solbrief.db import session_scope
from solbrief.db.models import (
    CardEvidence,
    CardExpert,
    CardLimitation,
    CardStatus,
    CardVersion,
    EvidenceGrade,
    Expert,
    ExpertStance,
    ItemType,
    SolutionCard,
)
from solbrief.verify import Document, check_support, http_fetcher, verify_card
from solbrief.verify.experts import check_expert
from solbrief.verify.sources import effective_cap, is_blocked, is_public_url, publisher_id
from solbrief.verify.spans import check_claim, check_span, numbers

NOW = datetime(2026, 11, 3, tzinfo=UTC)
PAGE = (
    "About the review. The CPSTF finding is based on evidence from a systematic review of 26 studies "
    "(search period through February 2018). Studies included a median of 1,234 participants (45 studies). "
    "Contact: Dr. Maria Lopez, Professor of Public Health, Northwestern University."
)


def doc(text=PAGE, status=200, url="https://thecommunityguide.cdc.gov/x"):
    return Document(url, status, text, "sha", NOW)


# --------------------------------------------------------------------------- spans


def test_numbers_normalized():
    assert numbers("1,234 people, 41% and 3.5 points; 2018.") == {"1234": 1, "41%": 1, "3.5": 1, "2018": 1}


def test_span_tolerates_typography_and_whitespace():
    span = "The  CPSTF finding is based on evidence from a systematic review of 26 studies (search period through"
    assert check_span(span.replace("CPSTF", "CPSTF®"), PAGE).ok


def test_near_exact_alignment_still_requires_exact_numbers():
    span = "Studies included a median of 1,243 participants (45 studies)."
    result = check_span(span, PAGE)
    assert not result.ok and result.reason in ("span_number_mismatch", "span_not_found")


@pytest.mark.parametrize(
    "claim,expected",
    [
        ("The review covered 26 studies.", None),
        ("The review covered 27 studies.", "claim_numbers_not_in_span"),
        ("The review did not find benefits.", "negation_mismatch"),
    ],
)
def test_check_claim(claim, expected):
    assert check_claim(claim, "a systematic review of 26 studies (search period through February 2018)") == expected


def test_entailment_hook_can_only_drop_and_outage_never_verifies():
    span = "based on evidence from a systematic review of 26 studies (search period through February 2018)"
    fetch = lambda url: doc()  # noqa: E731
    assert check_support("A review of 26 studies.", span, "https://cdc.gov/x", fetch, lambda c, s: True).ok
    assert (
        check_support("A review of 26 studies.", span, "https://cdc.gov/x", fetch, lambda c, s: False).reason
        == "not_entailed"
    )

    def down(c, s):
        raise RuntimeError("api down")

    assert (
        check_support("A review of 26 studies.", span, "https://cdc.gov/x", fetch, down).reason
        == "entailment_unavailable"
    )


# --------------------------------------------------------------------------- sources & grading


def test_publisher_identity_folds_agencies_and_groups_dois():
    assert publisher_id("https://thecommunityguide.cdc.gov/a") == publisher_id("https://www.cdc.gov/b")
    assert publisher_id("https://ies.ed.gov/ncee/wwc/x") == "U.S. Department of Education"
    assert publisher_id("https://doi.org/10.1016/j.x.2020.1") == publisher_id("https://doi.org/10.1016/j.y.2021.2")
    assert publisher_id("https://doi.org/10.1016/j.x") != publisher_id("https://doi.org/10.1093/j.x")


def test_blocked_sources():
    assert is_blocked("https://www.cochranelibrary.com/cdsr/doi/x") and is_blocked(
        "https://storytracker.solutionsjournalism.org/s/1"
    )
    assert not is_blocked("https://thecommunityguide.cdc.gov/x")


def test_grade_caps_check_declared_type_against_domain_and_document():
    assert (
        effective_cap("systematic_review", "https://thecommunityguide.cdc.gov/x", PAGE)
        == EvidenceGrade.meta_analysis_or_rct
    )
    # A newspaper cannot carry an RCT-grade claim, whatever the drafter declares
    assert effective_cap("rct", "https://www.nytimes.com/2026/x", "a randomized trial") == EvidenceGrade.anecdotal
    # Declared RCT on a .gov page that never mentions randomization → treated as a government report
    assert (
        effective_cap("rct", "https://www.hud.gov/x", "program overview") == EvidenceGrade.observational_or_admin_data
    )
    # Research-type claim from an unknown org domain → org report cap
    assert (
        effective_cap("systematic_review", "https://example-advocacy.org/x", "systematic review")
        == EvidenceGrade.self_reported
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/admin",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "ftp://example.org/x",
        "http://[::1]/",
    ],
)
def test_private_or_non_http_urls_are_never_fetched(url):
    assert not is_public_url(url)


# --------------------------------------------------------------------------- experts


def test_check_expert():
    fetch = lambda url: doc()  # noqa: E731
    url = "https://www.northwestern.edu/people/lopez"
    assert check_expert("Maria Lopez", "Northwestern University", url, fetch).ok
    assert check_expert("Maria Lopez", "Stanford University", url, fetch).reason == "institution_not_on_contact_page"
    assert check_expert("Jane Placeholder", "Northwestern University", url, fetch).reason == "name_not_on_contact_page"
    assert check_expert("Maria Lopez", None, "mailto:m@nu.edu", fetch).reason == "contact_not_institutional_url"
    assert check_expert("Maria Lopez", None, url, lambda u: doc(status=404)).reason == "contact_page_unreachable"


# --------------------------------------------------------------------------- fetch cache


def test_http_fetcher_caches_and_reports_missing_sources(monkeypatch):
    monkeypatch.setattr(fetch_mod, "is_public_url", lambda url: True)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/gone":
            return httpx.Response(404)
        return httpx.Response(200, html="<html><body><article><p>" + PAGE + "</p></article></body></html>")

    client = PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)
    fetch = http_fetcher(client)
    first = fetch("https://example.gov/page")
    assert first.ok and "1,234 participants" in first.text
    assert fetch("https://example.gov/page").text == first.text and len(calls) == 1  # served from cache
    assert fetch("https://example.gov/gone").status == 404
    fresh = http_fetcher(client, max_age=timedelta(0))
    fresh("https://example.gov/page")
    assert len(calls) == 3  # forced refetch


def test_http_fetcher_refuses_private_hosts():
    assert http_fetcher()("http://169.254.169.254/latest/meta-data").status == 0


# --------------------------------------------------------------------------- card lifecycle


SPANS = {
    "response": "The CPSTF finding is based on evidence from a systematic review of 26 studies (search period through",
    "evidence": "Studies included a median of 1,234 participants (45 studies).",
}


def _make_card(s, url_a, url_b):
    card = SolutionCard(
        slug=f"c-{uuid.uuid4().hex[:8]}",
        title="t",
        item_type=ItemType.solution,
        response_what="A program reviewed across 26 studies.",
        insight_mechanism="Mechanism text.",
    )
    s.add(card)
    s.flush()
    s.add_all(
        [
            CardEvidence(
                card_id=card.id,
                pillar="response",
                claim="Reviewed across 26 studies.",
                source_url=url_a,
                quoted_support_span=SPANS["response"],
            ),
            CardEvidence(
                card_id=card.id,
                pillar="insight",
                claim="Studies were large.",
                source_url=url_b,
                quoted_support_span=SPANS["evidence"],
            ),
            CardEvidence(
                card_id=card.id,
                pillar="evidence",
                claim="Median of 1,234 participants.",
                source_url=url_a,
                quoted_support_span=SPANS["evidence"],
                source_type="systematic_review",
            ),
            CardLimitation(
                card_id=card.id,
                limitation="The evidence comes from a review with a fixed search period.",
                source_url=url_a,
                quoted_support_span=SPANS["response"],
            ),
        ]
    )
    s.flush()
    return card


def test_reverification_marks_card_stale_when_source_disappears():
    a, b = "https://thecommunityguide.cdc.gov/x", "https://www.northwestern.edu/y"
    pages = {a: doc(url=a), b: doc(url=b)}
    with session_scope() as s:
        card = _make_card(s, a, b)
        report = verify_card(s, card, lambda u: pages[u], now=NOW)
        assert report.status == "verified" and report.publishable, report.reasons
        card_id = card.id
    pages[a] = doc(status=404, url=a)
    with session_scope() as s:
        card = s.get(SolutionCard, card_id)
        report = verify_card(s, card, lambda u: pages[u], now=NOW + timedelta(days=200))
        assert card.status == CardStatus.stale and not card.publishable
        assert "lost_verified_support" in report.reasons
        versions = s.scalars(select(CardVersion.reason).where(CardVersion.card_id == card_id)).all()
        assert versions == ["verifier: verified", "verifier: stale"]


def test_card_goes_stale_when_all_experts_move():
    a, b = "https://thecommunityguide.cdc.gov/x", "https://www.northwestern.edu/y"
    contact = "https://www.northwestern.edu/people/lopez"
    pages = {a: doc(url=a), b: doc(url=b), contact: doc(url=contact)}
    with session_scope() as s:
        card = _make_card(s, a, b)
        ex = Expert(
            name="Maria Lopez",
            institution="Northwestern University",
            stance=ExpertStance.researcher,
            contact_path_url=contact,
            source_url=b,
        )
        s.add(ex)
        s.flush()
        s.add(CardExpert(card_id=card.id, expert_id=ex.id, relation="evaluator_author"))
        s.flush()
        verify_card(s, card, lambda u: pages[u], now=NOW)
        assert ex.status == "active" and card.status == CardStatus.verified
        card_id, ex_id = card.id, ex.id
    pages[contact] = doc(text="This faculty page has been removed.", url=contact)
    with session_scope() as s:
        card = s.get(SolutionCard, card_id)
        report = verify_card(s, card, lambda u: pages[u], now=NOW + timedelta(days=200))
        assert s.get(Expert, ex_id).status == "moved"
        assert card.status == CardStatus.stale and "all_experts_moved" in report.reasons


def test_retracted_cards_are_left_alone():
    with session_scope() as s:
        card = _make_card(s, "https://cdc.gov/a", "https://nu.edu/b")
        card.status = CardStatus.retracted
        report = verify_card(s, card, lambda u: doc(url=u), now=NOW)
        assert report.status == "retracted" and card.status == CardStatus.retracted


def test_verify_stage_checks_new_cards(monkeypatch):
    import solbrief.pipeline.verify_stage as stage_mod
    from solbrief.pipeline.stages import StageContext

    a, b = "https://thecommunityguide.cdc.gov/x", "https://www.northwestern.edu/y"
    with session_scope() as s:
        _make_card(s, a, b)
    monkeypatch.setattr(stage_mod, "http_fetcher", lambda client, max_age=None: lambda u: doc(url=u))
    result = stage_mod.verify(StageContext(run_date=NOW.date(), dry_run=True))
    assert result.counts["new"] == 1 and result.counts["new:verified"] == 1
    # second run: nothing new, nothing due for re-verification
    again = stage_mod.verify(StageContext(run_date=NOW.date(), dry_run=True))
    assert again.counts["new"] == 0 and again.counts["reverify"] == 0


def test_pubmed_articles_are_read_through_eutils_and_emails_are_scrubbed():
    from solbrief.verify.fetch import extract_text, retrieval_url

    assert retrieval_url("https://pubmed.ncbi.nlm.nih.gov/22652205/").startswith(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=22652205"
    )
    assert retrieval_url("https://thecommunityguide.cdc.gov/x") == "https://thecommunityguide.cdc.gov/x"
    text = extract_text(b"Author information: (1) McMaster University. jim.dunn@mcmaster.ca\nAbstract.", "text/plain")
    assert "@" not in text and "[email removed]" in text
