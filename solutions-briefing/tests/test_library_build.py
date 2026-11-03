"""library-build budget enforcement and golden-set tooling (snapshot round trip, weight fitting)."""

import json

import pytest
from test_library import good_draft

from solbrief.agents.drafter import DraftResult
from solbrief.db import session_scope
from solbrief.library.build import build_library, plan_seeds
from solbrief.library.draft import CardDraft
from solbrief.matcher import default_weights
from solbrief.matcher.golden import build_snapshot, evaluate, fit_weights, load_pairs, load_snapshot


def test_plan_spans_many_classes():
    plan = plan_seeds(20)
    assert len(plan) == 20
    assert len({c for c, _ in plan}) >= 12
    assert len({s.url for _, s in plan}) == 20  # no seed twice


def test_zero_budget_refuses_to_spend():
    calls = []

    async def draft(seed, class_id):
        calls.append(seed)
        return DraftResult(None, "x", 0.5)

    report = build_library(5, budget_usd=0, draft_fn=draft)
    assert not calls and "authorize" in report.stopped_because


def test_budget_is_never_exceeded(monkeypatch):
    from solbrief.config import get_settings

    monkeypatch.setattr(get_settings(), "drafter_budget_usd", 2.0)
    costs = iter([1.9, 1.8, 2.0, 1.7, 1.9, 1.9])

    async def draft(seed, class_id):
        d = good_draft(title=f"Card from {seed.title}"[:100], problem_class_ids=[class_id])
        return DraftResult(CardDraft.model_validate(d), None, next(costs))

    report = build_library(10, budget_usd=8.0, draft_fn=draft)
    # A session starts only if spent + $2 cap ≤ $8: 1.9, 1.8, 2.0, 1.7 → $7.40; a 5th could reach $9.40, so stop.
    assert report.drafted == 4 and report.spent_usd == pytest.approx(7.4)
    assert report.spent_usd <= 8.0 and "budget" in report.stopped_because


def test_crashes_are_charged_the_full_cap(monkeypatch):
    from solbrief.config import get_settings

    monkeypatch.setattr(get_settings(), "drafter_budget_usd", 2.0)

    async def draft(seed, class_id):
        raise RuntimeError("cli crashed")

    report = build_library(10, budget_usd=5.0, draft_fn=draft)
    assert report.attempted == 2 and report.spent_usd == 4.0 and report.failures["crash"] == 2


def test_snapshot_round_trip_and_fit(tmp_path):
    from solbrief.matcher.golden import GOLDEN_DIR

    doc, pairs = load_pairs()
    with session_scope() as s:
        load_snapshot(s, GOLDEN_DIR / "library.json")
        snap = build_snapshot(s, pairs)
        before = evaluate(s, pairs).recall_all
        weights, base, after = fit_weights(s, pairs, default_weights(), rounds=1)
        s.rollback()
    assert len(snap["cards"]) == 12 and snap["query_embeddings"] == {}
    assert base == before and after >= base
    assert set(weights) == set(default_weights())
    path = tmp_path / "library.json"
    path.write_text(json.dumps(snap))
    with session_scope() as s:
        assert len(load_snapshot(s, path)["cards"]) == 12
        s.rollback()
