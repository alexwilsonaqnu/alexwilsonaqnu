"""Trust rules enforced by the database itself, not by prompts."""

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from solbrief.db import get_engine


def _exec(sql: str, **params):
    with get_engine().begin() as conn:
        conn.execute(text(sql), params)


@pytest.mark.parametrize(
    "contact",
    [
        "mailto:someone@university.edu",
        "tel:+13125550100",
        "https://example.edu/people/jane?email=jane@example.edu",
        "http://example.edu/media",  # must be https
    ],
)
def test_expert_contact_must_be_institutional_https_url(contact):
    with pytest.raises(IntegrityError):
        _exec(
            "INSERT INTO experts (name, stance, contact_path_url, source_url) "
            "VALUES ('X', 'researcher', :c, 'https://example.edu')",
            c=contact,
        )


def test_expert_institutional_url_accepted():
    _exec(
        "INSERT INTO experts (name, stance, contact_path_url, source_url) "
        "VALUES ('X', 'researcher', 'https://news.example.edu/media-relations', 'https://example.edu')"
    )
    _exec("DELETE FROM experts")


def test_card_cannot_be_verified_without_verification_and_grade():
    with pytest.raises(IntegrityError):
        _exec(
            "INSERT INTO solution_cards (slug, title, item_type, response_what, status) "
            "VALUES ('s', 't', 'solution', 'w', 'verified')"
        )


def test_evidence_cannot_be_verified_without_span_match():
    cid = uuid.uuid4()
    _exec(
        "INSERT INTO solution_cards (id, slug, title, item_type, response_what) "
        "VALUES (:id, 's2', 't', 'solution', 'w')",
        id=cid,
    )
    try:
        with pytest.raises(IntegrityError):
            _exec(
                "INSERT INTO card_evidence (card_id, claim, source_url, quoted_support_span, verified) "
                "VALUES (:id, 'c', 'https://x.gov', 'span', true)",
                id=cid,
            )
    finally:
        _exec("DELETE FROM solution_cards WHERE id = :id", id=cid)


def test_dry_run_briefing_can_never_be_marked_sent():
    rid = uuid.uuid4()
    _exec("INSERT INTO readers (id, email) VALUES (:id, 'r@example.org')", id=rid)
    try:
        with pytest.raises(IntegrityError):
            _exec(
                "INSERT INTO briefings (reader_id, date, dry_run, sent_at) VALUES (:id, :d, true, now())",
                id=rid,
                d=date(2026, 11, 2),
            )
    finally:
        _exec("DELETE FROM readers WHERE id = :id", id=rid)


def test_agents_may_only_propose_taxonomy_changes():
    with pytest.raises(IntegrityError):
        _exec(
            "INSERT INTO taxonomy_changes (change_type, class_id, actor) "
            "VALUES ('accept', 'housing', 'agent')"
        )
    _exec(
        "INSERT INTO taxonomy_changes (change_type, class_id, actor) VALUES ('propose', 'housing', 'agent')"
    )
    _exec(
        "INSERT INTO taxonomy_changes (change_type, class_id, actor) "
        "VALUES ('accept', 'housing', 'human:editor@knightlab')"
    )
    _exec("DELETE FROM taxonomy_changes")


def test_reader_email_is_case_insensitive_unique():
    _exec("INSERT INTO readers (email) VALUES ('Reporter@Example.org')")
    try:
        with pytest.raises(IntegrityError):
            _exec("INSERT INTO readers (email) VALUES ('reporter@example.org')")
    finally:
        _exec("DELETE FROM readers")
