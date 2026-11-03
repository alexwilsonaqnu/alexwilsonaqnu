"""Verifier trap eval — CI hard gate.

Every deliberately unsupported claim in cases.yaml must be rejected (100%), with one of its expected reason
codes; every genuine quotation must pass; card-level outcomes (status, publishable, grade) must match.
Runs offline against recorded real sources (sources/index.json).
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from solbrief.db import session_scope
from solbrief.db.models import CardEvidence, CardLimitation, ItemType, SolutionCard
from solbrief.verify import Document, verify_card

HERE = Path(__file__).parent / "verifier_traps"
SPEC = yaml.safe_load((HERE / "cases.yaml").read_text())
INDEX = json.loads((HERE / "sources" / "index.json").read_text())
FETCHED_AT = datetime(2026, 11, 3, tzinfo=UTC)
RESULTS: list[dict] = []


def fixture_fetch(url: str) -> Document:
    entry = INDEX.get(url)
    if entry is None:
        return Document(url, 0, "", None, FETCHED_AT)
    if entry["status"] != 200:
        return Document(url, entry["status"], "", None, FETCHED_AT)
    text = (HERE / "sources" / entry["file"]).read_text(encoding="utf-8")
    return Document(url, 200, text, "fixture", FETCHED_AT)


def build_case(case: dict) -> dict:
    card = copy.deepcopy(SPEC["base"])
    card.update(case.get("changes", {}))
    supports = [s for s in card["supports"] if s["id"] not in case.get("remove_supports", [])]
    for key, replacement in case.get("replace_supports", {}).items():
        supports = [replacement if s["id"] == key else s for s in supports]
    supports += case.get("add_supports", [])
    limitations = [lim for lim in card["limitations"] if lim["id"] not in case.get("remove_limitations", [])]
    limitations += case.get("add_limitations", [])
    card["supports"], card["limitations"] = supports, limitations
    card["expect_card"] = case.get("expect_card", SPEC["base"]["expect_card"])
    return card


@pytest.mark.parametrize("case", SPEC["cases"], ids=[c["name"] for c in SPEC["cases"]])
def test_trap_case(case):
    card_spec = build_case(case)
    with session_scope() as s:
        card = SolutionCard(
            slug=f"trap-{case['name']}-{uuid.uuid4().hex[:6]}",
            title=card_spec["title"],
            item_type=ItemType.solution,
            response_what=card_spec["response_what"],
            insight_mechanism=card_spec["insight_mechanism"],
            insight_transferability=card_spec.get("insight_transferability"),
            problem_class_ids=["homelessness"],
        )
        s.add(card)
        s.flush()
        ev_rows = {}
        for sup in card_spec["supports"]:
            row = CardEvidence(
                card_id=card.id,
                pillar=sup["pillar"],
                claim=sup["claim"],
                source_url=sup["url"],
                quoted_support_span=sup["span"],
                source_type=sup.get("source_type"),
            )
            s.add(row)
            ev_rows[sup["id"]] = (sup, row)
        lim_rows = {}
        for lim in card_spec["limitations"]:
            row = CardLimitation(
                card_id=card.id,
                limitation=lim["limitation"],
                source_url=lim["url"],
                quoted_support_span=lim.get("span"),
            )
            s.add(row)
            lim_rows[lim["id"]] = (lim, row)
        s.flush()
        report = verify_card(s, card, fixture_fetch)

        problems = []
        for kind, rows in (("support", ev_rows), ("limitation", lim_rows)):
            for sid, (spec, row) in rows.items():
                outcome = {
                    "case": case["name"],
                    "kind": kind,
                    "id": sid,
                    "expect": spec["expect"],
                    "verified": row.verified,
                    "reason": row.failure_reason,
                }
                RESULTS.append(outcome)
                if spec["expect"] == "ok" and not row.verified:
                    problems.append(f"{kind} {sid}: genuine quote rejected ({row.failure_reason})")
                if spec["expect"] == "fail":
                    if row.verified:
                        problems.append(f"{kind} {sid}: UNSUPPORTED CLAIM WAS VERIFIED")
                    elif row.failure_reason not in spec["reasons"]:
                        problems.append(f"{kind} {sid}: rejected for {row.failure_reason}, expected {spec['reasons']}")
        exp = card_spec["expect_card"]
        if report.status != exp["status"]:
            problems.append(f"card status {report.status} != {exp['status']} ({report.reasons})")
        if report.publishable != exp["publishable"]:
            problems.append(f"card publishable {report.publishable} != {exp['publishable']} ({report.reasons})")
        if "grade" in exp and report.grade != exp["grade"]:
            problems.append(f"card grade {report.grade} != {exp['grade']}")
        if "reason" in exp and exp["reason"] not in report.reasons:
            problems.append(f"expected card reason {exp['reason']} in {report.reasons}")
        assert not problems, "\n".join(problems)


def test_zz_catch_rate_is_100_percent():
    """Summary gate (runs after the cases): every trap caught, no genuine quote rejected."""
    traps = [r for r in RESULTS if r["expect"] == "fail"]
    genuine = [r for r in RESULTS if r["expect"] == "ok"]
    caught = sum(1 for r in traps if not r["verified"])
    false_rejects = [r for r in genuine if not r["verified"]]
    print(
        f"\nverifier traps: {caught}/{len(traps)} caught; genuine quotes rejected: {len(false_rejects)}/{len(genuine)}"
    )
    assert traps, "trap cases did not run"
    assert caught == len(traps)
    assert not false_rejects
