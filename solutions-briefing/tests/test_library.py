"""Library research: seeds, draft contract, drafter session wiring (fake SDK query), storage, stage behaviour."""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage
from pydantic import ValidationError
from sqlalchemy import select

from solbrief.agents import drafter
from solbrief.db import session_scope
from solbrief.db.models import CardEvidence, CardExpert, CardLimitation, Expert, ItemType, Problem, SolutionCard
from solbrief.library.draft import CardDraft, store_draft
from solbrief.library.seeds import WWC_BASE, all_seeds, build_wwc_seeds, seeds_for
from solbrief.pipeline.library_stage import library_research, thin_classes, unused_seeds
from solbrief.pipeline.stages import StageContext
from solbrief.taxonomy import class_ids
from solbrief.verify import Document, verify_card

HF = "https://thecommunityguide.cdc.gov/findings/social-determinants-health-housing-first-programs.html"
PM = "https://pubmed.ncbi.nlm.nih.gov/22652205/"
SOURCES = Path(__file__).parent.parent / "evals" / "verifier_traps" / "sources"


def good_draft(**overrides) -> dict:
    d = {
        "title": "Housing First programs",
        "response_what": "Housing First programs give people experiencing homelessness subsidized, permanent housing.",
        "insight_mechanism": "Housing comes first; services are offered, not required.",
        "insight_transferability": "Evidence is mostly from urban and suburban programs.",
        "response_quotes": [
            {
                "claim": "Programs provide subsidized, time-unlimited housing.",
                "source_url": HF,
                "span": "Housing First programs provide regular, subsidized, time-unlimited housing to "
                "individuals and families experiencing homelessness",
            }
        ],
        "insight_quotes": [
            {
                "claim": "The model rests on a theory of housing that contradicts the dominant model.",
                "source_url": PM,
                "span": "is based on a theory of the role of housing in living with mental illness that "
                "has a number of elements that directly contradict the theory underlying the "
                "dominant model",
            }
        ],
        "evidence": [
            {
                "claim": "Clients had a median 41% greater housing stability (13 studies).",
                "source_url": HF,
                "span": "Median 41% greater housing stability (13 studies)",
                "source_type": "systematic_review",
            }
        ],
        "limitations": [
            {
                "limitation": "No included study took place in a rural setting.",
                "source_url": HF,
                "span": "None of the included studies were conducted in rural settings.",
            }
        ],
        "problem_class_ids": ["homelessness"],
        "experts": [],
        "proposed_grade": "meta_analysis_or_rct",
    }
    d.update(overrides)
    return d


def fixture_fetch(url):
    import json

    index = json.loads((SOURCES / "index.json").read_text())
    entry = index.get(url)
    if not entry or entry["status"] != 200:
        return Document(url, entry["status"] if entry else 0, "", None, datetime.now(UTC))
    return Document(url, 200, (SOURCES / entry["file"]).read_text(), "x", datetime.now(UTC))


# --------------------------------------------------------------------------- seeds


def test_seeds_are_valid_and_cover_classes():
    seeds = all_seeds()
    assert len(seeds) > 100
    assert all(set(s.class_ids) <= class_ids() for s in seeds)
    assert all(s.url.startswith("https://") for s in seeds)
    assert len({s.url for s in seeds}) == len(seeds)
    assert seeds_for("homelessness") and seeds_for("literacy-learning-recovery")
    assert not any("solutionsjournalism.org" in s.url or "cochrane" in s.url for s in seeds)


def test_build_wwc_seeds_keeps_only_positive_mapped_domains(tmp_path):
    csv = tmp_path / "wwc.csv"
    csv.write_text(
        "i_InterventionID,i_Intervention_Name,i_Outcome_Domain,i_Effectiveness_Rating,i_Intervention_Page_URL\n"
        "1,Good Reading,Comprehension,Positive Effects,/intervention/1\n"
        "1,Good Reading,Alphabetics,Potentially Positive Effects,/intervention/1\n"
        "2,Null Program,Comprehension,No Discernible Effects,/intervention/2\n"
        "3,Unmapped,Science Fiction,Positive Effects,/intervention/3\n"
        "4,Attendance Plus,School Attendance,Positive Effects,/intervention/4\n",
        encoding="utf-8",
    )
    doc = build_wwc_seeds(csv)
    urls = {s["url"]: s for s in doc["seeds"]}
    assert set(urls) == {f"{WWC_BASE}/intervention/1", f"{WWC_BASE}/intervention/4"}
    assert urls[f"{WWC_BASE}/intervention/1"]["notes"]["ratings"] == {
        "Comprehension": "Positive Effects",
        "Alphabetics": "Potentially Positive Effects",
    }
    assert urls[f"{WWC_BASE}/intervention/4"]["class_ids"] == ["chronic-absenteeism"]


# --------------------------------------------------------------------------- draft contract


def test_draft_contract_rejects_unknown_class_and_personal_contacts():
    CardDraft.model_validate(good_draft())
    with pytest.raises(ValidationError, match="unknown taxonomy classes"):
        CardDraft.model_validate(good_draft(problem_class_ids=["made-up"]))
    expert = {
        "name": "Maria Lopez",
        "institution": "Northwestern University",
        "stance": "researcher",
        "source_url": HF,
        "contact_path_url": "mailto:maria@northwestern.edu",
    }
    with pytest.raises(ValidationError, match="institutional page"):
        CardDraft.model_validate(good_draft(experts=[expert]))
    with pytest.raises(ValidationError):  # every pillar needs at least one quote
        CardDraft.model_validate(good_draft(limitations=[]))


def test_store_draft_is_unverified_until_the_verifier_runs():
    expert = {
        "name": "Maria Lopez",
        "institution": "Northwestern University",
        "stance": "researcher",
        "source_url": HF,
        "contact_path_url": "https://www.northwestern.edu/people/lopez",
    }
    draft = CardDraft.model_validate(good_draft(experts=[expert]))
    with session_scope() as s:
        card = store_draft(s, draft, uuid.uuid4())
        assert card.publishable is False and card.verification is None and card.status.value == "provisional"
        rows = s.scalars(select(CardEvidence).where(CardEvidence.card_id == card.id)).all()
        assert sorted(r.pillar for r in rows) == ["evidence", "insight", "response"]
        assert not any(r.verified for r in rows)
        assert s.scalar(select(CardLimitation).where(CardLimitation.card_id == card.id)).verified is False
        ex = s.scalar(select(Expert))
        assert ex.status == "unverified"
        assert s.scalar(select(CardExpert.relation)) == "evaluator_author"
        # the same draft, verified against the recorded real sources, publishes as verified (CDC + NIH)
        report = verify_card(s, card, fixture_fetch)
        assert report.status == "verified" and report.publishable, report.reasons


# --------------------------------------------------------------------------- drafter session wiring


def test_system_prompt_carries_knowledge_packs_and_rules():
    prompt = drafter.system_prompt()
    assert "The four pillars of a solution card" in prompt and "Evidence grading rubric" in prompt
    assert "copied character-for-character" in prompt and "homelessness:" in prompt
    assert "name: four-pillars" not in prompt  # frontmatter stripped


def test_draft_card_runs_isolated_session_and_validates_output():
    captured = {}

    async def fake_query(*, prompt, options):
        captured["prompt"], captured["options"] = prompt, options
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=7,
            session_id="s",
            total_cost_usd=0.42,
            structured_output=good_draft(),
        )

    seed = seeds_for("homelessness")[0]
    result = asyncio.run(drafter.draft_card(seed, "homelessness", fetch=fixture_fetch, query_fn=fake_query))
    assert result.draft is not None and result.draft.title == "Housing First programs"
    opts = captured["options"]
    assert opts.tools == []  # no built-in tools: no shell, files or web search
    assert set(opts.allowed_tools) == {"mcp__library__fetch_source", "mcp__library__search_openalex"}
    assert opts.output_format["type"] == "json_schema" and opts.max_budget_usd > 0
    assert seed.url in captured["prompt"]


def test_draft_card_rejects_invalid_or_missing_output():
    async def bad(*, prompt, options):
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=3,
            session_id="s",
            structured_output=good_draft(problem_class_ids=["nope"]),
        )

    async def budget_hit(*, prompt, options):
        yield ResultMessage(
            subtype="error_max_budget_usd",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=30,
            session_id="s",
        )

    seed = seeds_for("homelessness")[0]
    assert "invalid draft" in asyncio.run(drafter.draft_card(seed, "homelessness", fixture_fetch, bad)).error
    assert "without a card" in asyncio.run(drafter.draft_card(seed, "homelessness", fixture_fetch, budget_hit)).error


def test_fetch_tool_refuses_blocked_sources_and_reports_dead_links(monkeypatch):
    import claude_agent_sdk

    handlers = {}

    def capture_tool(name, desc, schema):
        def deco(fn):
            handlers[name] = fn
            return fn

        return deco

    monkeypatch.setattr(claude_agent_sdk, "tool", capture_tool)
    monkeypatch.setattr(claude_agent_sdk, "create_sdk_mcp_server", lambda name, tools: {})
    drafter.build_tools(fixture_fetch)

    def call(url):
        return asyncio.run(handlers["fetch_source"]({"url": url}))["content"][0]["text"]

    assert call("https://www.cochranelibrary.com/x").startswith("REFUSED")
    assert call("https://thecommunityguide.cdc.gov/findings/retired-finding-that-no-longer-exists.html").startswith(
        "UNAVAILABLE"
    )
    assert "Median 41% greater housing stability" in call(HF)


# --------------------------------------------------------------------------- stage


def test_library_stage_reports_thin_classes_and_skips_without_key():
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    with session_scope() as s:
        for cls in (["homelessness"], ["tornadoes-severe-storms"], ["drought"]):
            s.add(
                Problem(
                    run_date=now.date(),
                    cluster_id=uuid.uuid4(),
                    title="p",
                    source_adapter="x",
                    first_seen=now,
                    last_seen=now,
                    problem_class_ids=cls,
                    item_type=ItemType.solution,
                )
            )
    thin = [c for c, _, _ in thin_classes(now.date())]
    assert set(thin) == {"homelessness", "tornadoes-severe-storms", "drought"}
    result = library_research(StageContext(run_date=now.date(), dry_run=True))
    assert result.status.value == "skipped"
    assert set(result.counts["no_seed_sources"]) == {"tornadoes-severe-storms", "drought"}


def test_unused_seeds_excludes_already_cited_sources():
    before = unused_seeds("homelessness")
    with session_scope() as s:
        store_draft(s, CardDraft.model_validate(good_draft()))
    after = unused_seeds("homelessness")
    assert HF in {x.url for x in before} and HF not in {x.url for x in after}
    assert len(after) == len(before) - 1
    with session_scope() as s:
        assert s.scalar(select(SolutionCard.title)) == "Housing First programs"


def test_stored_draft_is_picked_up_by_verify_stage(monkeypatch):
    """Regression: a draft's verification must be SQL NULL (not JSON null) so the stage sees it as new."""
    import solbrief.pipeline.verify_stage as stage_mod

    with session_scope() as s:
        store_draft(s, CardDraft.model_validate(good_draft()))
    monkeypatch.setattr(stage_mod, "http_fetcher", lambda client, max_age=None: fixture_fetch)
    result = stage_mod.verify(StageContext(run_date=datetime(2026, 11, 3).date(), dry_run=True))
    assert result.counts["new"] == 1 and result.counts["new:verified"] == 1
