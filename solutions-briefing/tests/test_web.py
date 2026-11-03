from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from solbrief.db import get_engine
from solbrief.pipeline.daily import run_daily

AUTH = {"Authorization": "Bearer test-admin-token"}


@pytest.fixture
def client():
    from solbrief.web.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_healthz_reports_schema_revision(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert body["ok"] is True and body["schema_revision"] == head


def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "Solutions Briefing" in r.text


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "test-admin-token"}])
def test_admin_requires_bearer_token(client, headers):
    assert client.get("/admin/runs/2026-11-02", headers=headers).status_code == 401


def test_admin_disabled_without_token(client, monkeypatch):
    from solbrief.config import get_settings

    monkeypatch.setattr(get_settings(), "admin_token", None)
    assert client.get("/admin/runs/2026-11-02", headers=AUTH).status_code == 503


def test_trigger_run_enqueues_once(client):
    r = client.post("/admin/runs", json={"date": "2026-11-02", "dry_run": True}, headers=AUTH)
    assert r.status_code == 202, r.text
    assert r.json()["dry_run"] is True
    dup = client.post("/admin/runs", json={"date": "2026-11-02", "dry_run": True}, headers=AUTH)
    assert dup.status_code == 409
    with get_engine().connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM procrastinate_jobs WHERE task_name='daily_run'")).scalar()
    assert n == 1


def test_staging_forces_dry_run(client, monkeypatch):
    from solbrief.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "staging")
    r = client.post("/admin/runs", json={"date": "2026-11-03", "dry_run": False}, headers=AUTH)
    assert r.status_code == 202 and r.json()["dry_run"] is True


def test_rerun_unknown_stage_404(client):
    r = client.post("/admin/runs/2026-11-02/stages/nope/rerun", headers=AUTH)
    assert r.status_code == 404


def test_run_status_lists_stages(client):
    run_daily(date(2026, 11, 2), dry_run=True)
    r = client.get("/admin/runs/2026-11-02", headers=AUTH)
    assert r.status_code == 200
    stages = {s["stage"] for s in r.json()["stages"]}
    assert "run_report" in stages and "ingest" in stages


def test_admin_problems_lists_ranked_feed(client):
    import uuid
    from datetime import UTC, datetime

    from solbrief.db import session_scope
    from solbrief.db.models import ItemType, Problem

    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    with session_scope() as s:
        for title, sev, fips in (("Minor thing", 0.25, ["17031"]), ("Tornado Warning", 1.0, ["40033"])):
            s.add(
                Problem(
                    run_date=now.date(),
                    cluster_id=uuid.uuid4(),
                    title=title,
                    source_adapter="nws",
                    first_seen=now,
                    last_seen=now,
                    severity=sev,
                    novelty=1.0,
                    locality_score=1.0,
                    fips=fips,
                    problem_class_ids=["tornadoes-severe-storms"],
                    item_type=ItemType.service,
                )
            )
    r = client.get("/admin/problems/2026-11-02", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert [p["title"] for p in body["problems"]] == ["Tornado Warning", "Minor thing"]
    assert body["problems"][0]["places"] == ["Cotton County, OK"]
    only_cook = client.get("/admin/problems/2026-11-02?fips=17031", headers=AUTH).json()
    assert [p["title"] for p in only_cook["problems"]] == ["Minor thing"]


def test_admin_cards_shows_verifier_outcomes(client):
    from solbrief.db import session_scope
    from solbrief.db.models import ItemType, SolutionCard

    with session_scope() as s:
        s.add(
            SolutionCard(
                slug="x-1",
                title="Draft card",
                item_type=ItemType.solution,
                response_what="w",
                problem_class_ids=["homelessness"],
                verification={
                    "reasons": ["pillar_unsupported:limitations"],
                    "publishers": [],
                    "supports": [{"ok": False, "reason": "span_not_found"}],
                },
            )
        )
    body = client.get("/admin/cards?publishable=false", headers=AUTH).json()
    assert body["count"] == 1
    card = body["cards"][0]
    assert card["reasons"] == ["pillar_unsupported:limitations"] and card["dropped"][0]["reason"] == "span_not_found"
    assert client.get("/admin/cards?publishable=true", headers=AUTH).json()["count"] == 0
