"""Ingest stage: classification, clustering, scoring, idempotent upsert, per-source failure isolation."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

import solbrief.adapters
from solbrief.adapters import AdapterSpec, SourceError, cdc_rss, nws, openfema
from solbrief.adapters.base import RawItem
from solbrief.classify import classify_items, rule_classes
from solbrief.db import session_scope
from solbrief.db.models import ItemType, Problem, RunStatus
from solbrief.pipeline.ingest import cluster_items, ingest, locality_score, novelty_score
from solbrief.pipeline.stages import StageContext

FIX = Path(__file__).parent / "fixtures"
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
D = date(2026, 11, 2)


class FixtureAdapter:
    def __init__(self, name, items):
        self.name, self._items = name, items

    def fetch(self, since):
        return list(self._items)


class BrokenAdapter:
    name = "broken"

    def fetch(self, since):
        raise SourceError("GET https://example.gov -> HTTP 403")


def _fixture_specs():
    nws_items = nws.parse(json.loads((FIX / "nws_alerts.json").read_text()), EPOCH)
    fema_rows = json.loads((FIX / "openfema_declarations.json").read_text())["DisasterDeclarationsSummaries"]
    fema_items = openfema.parse(fema_rows)
    cdc_items = cdc_rss.parse("cdc_outbreaks", (FIX / "cdc_outbreaks.rss").read_bytes(), EPOCH)

    def mk(name, items):
        return AdapterSpec(name, lambda c: FixtureAdapter(name, items), timedelta(days=1))

    return (mk("nws", nws_items), mk("openfema", fema_items), mk("cdc_outbreaks", cdc_items))


def _problems():
    with session_scope() as s:
        return s.scalars(select(Problem).where(Problem.run_date == D)).all()


def test_ingest_writes_classified_geotagged_problems(monkeypatch):
    monkeypatch.setattr(solbrief.adapters, "ADAPTERS", _fixture_specs())
    result = ingest(StageContext(run_date=D, dry_run=True))
    assert result.status == RunStatus.succeeded, result.errors
    rows = _problems()
    assert len(rows) == result.counts["problems_written"] > 0
    by_source = {r.source_adapter for r in rows}
    assert by_source == {"nws", "openfema", "cdc_outbreaks"}
    for r in rows:
        assert r.fips or r.is_national
        assert 0 <= r.locality_score <= 1 and 0 <= r.novelty <= 1
    # novelty reflects when the event happened: today's NWS alerts are new, older declarations less so
    assert all(r.novelty == 1.0 for r in rows if r.source_adapter == "nws")
    assert any(r.novelty < 1.0 for r in rows if r.source_adapter == "openfema")
    # CDC outbreak items are classified by keyword rules
    cdc = {r.title: r.problem_class_ids for r in rows if r.source_adapter == "cdc_outbreaks"}
    assert all("infectious-disease-outbreaks" in c for c in cdc.values())
    assert "foodborne-illness" in cdc["Salmonella Outbreak Linked to Raw Oysters"]
    assert "foodborne-illness" not in cdc["Salmonella Outbreak Linked to Turtles"]  # pets, not food
    assert "vaccination" in cdc["Measles Outbreaks 2025"]
    assert all(r.item_type == ItemType.service for r in rows if r.source_adapter == "cdc_outbreaks")
    # NWS alerts in the same office/event/day collapse into one problem
    nws_rows = [r for r in rows if r.source_adapter == "nws"]
    assert len(nws_rows) < result.counts["adapters"]["nws"]["items"]


def test_ingest_is_idempotent_per_day(monkeypatch):
    monkeypatch.setattr(solbrief.adapters, "ADAPTERS", _fixture_specs())
    ingest(StageContext(run_date=D, dry_run=True))
    first = sorted((r.cluster_id, r.title) for r in _problems())
    ingest(StageContext(run_date=D, dry_run=True))
    assert sorted((r.cluster_id, r.title) for r in _problems()) == first


def test_recurring_problem_keeps_cluster_id_and_novelty_decays(monkeypatch):
    item = RawItem(
        source="nws",
        external_id="a",
        cluster_key="Flood Warning|NWS Chicago IL|x",
        title="Flood",
        url=None,
        observed_at=datetime(2026, 11, 2, 12, tzinfo=UTC),
        item_type=ItemType.service,
        fips=["17031"],
        class_ids=["flooding"],
        severity=0.75,
    )
    spec = AdapterSpec("nws", lambda c: FixtureAdapter("nws", [item]), timedelta(days=1))
    monkeypatch.setattr(solbrief.adapters, "ADAPTERS", (spec,))
    ingest(StageContext(run_date=D, dry_run=True))
    ingest(StageContext(run_date=D + timedelta(days=4), dry_run=True))
    with session_scope() as s:
        rows = s.scalars(select(Problem).order_by(Problem.run_date)).all()
    assert len(rows) == 2 and rows[0].cluster_id == rows[1].cluster_id
    assert rows[0].novelty == 1.0 and rows[1].novelty == 0.25


def test_one_broken_source_makes_stage_partial_not_failed(monkeypatch):
    specs = (*_fixture_specs()[:1], AdapterSpec("broken", lambda c: BrokenAdapter(), timedelta(days=1)))
    monkeypatch.setattr(solbrief.adapters, "ADAPTERS", specs)
    result = ingest(StageContext(run_date=D, dry_run=True))
    assert result.status == RunStatus.partial
    assert result.errors[0]["adapter"] == "broken" and "403" in result.errors[0]["msg"]
    assert result.counts["adapters"]["broken"] == {"status": "failed"}
    assert _problems()  # the healthy source still wrote


def test_all_sources_broken_fails_stage(monkeypatch):
    specs = (AdapterSpec("broken", lambda c: BrokenAdapter(), timedelta(days=1)),)
    monkeypatch.setattr(solbrief.adapters, "ADAPTERS", specs)
    assert ingest(StageContext(run_date=D, dry_run=True)).status == RunStatus.failed


# --------------------------------------------------------------------------- classification


def _item(title, classes=()):
    return RawItem(
        source="cdc_mmwr",
        external_id=title,
        cluster_key=title,
        title=title,
        url=None,
        observed_at=EPOCH,
        item_type=ItemType.solution,
        is_national=True,
        class_ids=list(classes),
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Salmonella Outbreak Linked to Raw Oysters", ["foodborne-illness", "infectious-disease-outbreaks"]),
        (
            "Acute Kidney Injury Among Patients with Drug Overdose-Involved Hospitalizations",
            ["overdose-substance-use"],
        ),
        ("Measles cases among unvaccinated children", ["vaccination", "infectious-disease-outbreaks"]),
        ("Mental health of high school students", ["youth-mental-health"]),
        ("QuickStats: Percentage of adults who ever received a diagnosis", []),
    ],
)
def test_rule_classes(text, expected):
    assert rule_classes(text) == expected


def test_source_coded_classes_are_kept_and_rules_fill_the_rest():
    items = [
        _item("Flood", ["flooding"]),
        _item("Salmonella outbreak linked to raw milk"),
        _item("QuickStats: something"),
    ]
    counts = classify_items(items)
    assert items[0].class_ids == ["flooding"]
    assert "foodborne-illness" in items[1].class_ids
    assert items[2].class_ids == []
    assert counts == {"source_coded": 1, "rules": 1, "llm": 0, "unclassified": 1}


def test_llm_fallback_only_for_unmatched_and_cannot_invent_classes():
    seen = []

    def fake_llm(text):
        seen.append(text)
        return ["chronic-disease", "made-up-class", "drought", "air-quality", "eviction"]

    items = [_item("Salmonella outbreak"), _item("QuickStats: diagnosis prevalence")]
    counts = classify_items(items, llm=fake_llm)
    assert len(seen) == 1 and "QuickStats" in seen[0]
    assert items[1].class_ids == ["chronic-disease", "drought", "air-quality"]  # invalid dropped, max 3
    assert counts["llm"] == 1


def test_llm_outage_does_not_fail_classification():
    def down(text):
        raise RuntimeError("API unavailable")

    items = [_item("QuickStats: diagnosis prevalence")]
    assert classify_items(items, llm=down)["unclassified"] == 1


# --------------------------------------------------------------------------- scoring


def test_locality_score_monotonic():
    one = locality_score(["17031"], False)
    many = locality_score([f"17{n:03d}" for n in range(1, 40, 2)], False)
    statewide = locality_score(["17"], False)
    assert one == 1.0 and 0 < many < one and 0 < statewide < one
    assert locality_score([], True) == 0.0


def test_novelty_score():
    seen = datetime(2026, 11, 2, 3, tzinfo=UTC)
    assert novelty_score(seen, D) == 1.0
    assert novelty_score(seen, D + timedelta(days=2)) == 0.5


def test_cluster_items_groups_by_source_and_key():
    a, b, c = _item("x"), _item("x"), _item("y")
    assert len(cluster_items([a, b, c])) == 2
