"""Adapter contract tests on recorded (trimmed) live responses — no network."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from solbrief.adapters import ADAPTERS, PoliteClient, SourceError, cdc_rss, nws, openfema
from solbrief.db.models import ItemType
from solbrief.geo import is_county_fips, is_state_fips
from solbrief.taxonomy import class_ids

FIX = Path(__file__).parent / "fixtures"
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
VALID_CLASSES = class_ids()


def _contract(items):
    assert items, "fixture should yield items"
    for it in items:
        assert it.title and it.cluster_key and it.external_id
        assert it.observed_at.tzinfo is not None
        assert it.fips or it.is_national, f"{it.title}: no geography"
        for f in it.fips:
            assert is_county_fips(f) or is_state_fips(f), f
        assert set(it.class_ids) <= VALID_CLASSES, it.class_ids
        assert it.severity is None or 0 <= it.severity <= 1


# --------------------------------------------------------------------------- NWS


def test_nws_contract_and_filtering():
    payload = json.loads((FIX / "nws_alerts.json").read_text())
    items = nws.parse(payload, EPOCH)
    _contract(items)
    events = {it.raw["event"] for it in items}
    assert not events & nws.SKIP_EVENTS, "marine/vague products must be skipped"
    assert all(it.item_type == ItemType.service for it in items)
    assert all(it.class_ids for it in items)


def test_nws_same_codes_become_county_fips():
    payload = {
        "features": [
            {
                "id": "x",
                "properties": {
                    "id": "x",
                    "event": "Flood Warning",
                    "status": "Actual",
                    "severity": "Severe",
                    "sent": "2026-11-02T10:00:00-06:00",
                    "senderName": "NWS Chicago IL",
                    "headline": "h",
                    "geocode": {"SAME": ["017031", "017043", "999999"], "UGC": ["ILC031"]},
                },
            }
        ]
    }
    [item] = nws.parse(payload, EPOCH)
    assert item.fips == ["17031", "17043"]  # Cook, DuPage; invalid code dropped
    assert item.class_ids == ["flooding"] and item.severity == 0.75
    assert item.cluster_key == "Flood Warning|NWS Chicago IL|2026-11-02"


def test_nws_marine_only_alert_dropped():
    payload = {
        "features": [
            {
                "id": "y",
                "properties": {
                    "id": "y",
                    "event": "Flood Warning",
                    "status": "Actual",
                    "severity": "Minor",
                    "sent": "2026-11-02T10:00:00-06:00",
                    "geocode": {"SAME": [], "UGC": ["LMZ740"]},
                },
            }
        ]
    }
    assert nws.parse(payload, EPOCH) == []


def test_nws_since_filter():
    payload = json.loads((FIX / "nws_alerts.json").read_text())
    assert nws.parse(payload, datetime(2100, 1, 1, tzinfo=UTC)) == []


@pytest.mark.parametrize(
    "event,expected",
    [
        ("Coastal Flood Advisory", ("flooding", "coastal-hazards")),
        ("Excessive Heat Warning", ("extreme-heat",)),
        ("Extreme Heat Watch", ("extreme-heat",)),
        ("Blizzard Warning", ("extreme-cold-winter-storms",)),
        ("Tornado Warning", ("tornadoes-severe-storms",)),
        ("Red Flag Warning", ("wildfire-smoke",)),
        ("Air Quality Alert", ("air-quality",)),
        ("Rip Current Statement", ("coastal-hazards",)),
        ("Some New Product", ()),
    ],
)
def test_nws_event_rules(event, expected):
    assert nws.classify_event(event) == expected


# --------------------------------------------------------------------------- OpenFEMA


def test_openfema_groups_rows_by_declaration():
    rows = json.loads((FIX / "openfema_declarations.json").read_text())["DisasterDeclarationsSummaries"]
    items = openfema.parse(rows)
    _contract(items)
    assert len(items) == len({r["disasterNumber"] for r in rows})
    assert all("disaster-recovery-rebuilding" in it.class_ids for it in items)
    assert all(it.item_type == ItemType.solution for it in items)
    total_areas = sum(len(it.raw["designatedAreas"]) for it in items)
    assert total_areas == len(rows)


def test_openfema_statewide_code_becomes_state_fips():
    rows = [
        {
            "disasterNumber": 1,
            "femaDeclarationString": "EM-1-GA",
            "declarationType": "EM",
            "state": "GA",
            "incidentType": "Tropical Storm",
            "declarationTitle": "TROPICAL STORM X",
            "declarationDate": "2026-10-01T00:00:00.000Z",
            "fipsStateCode": "13",
            "fipsCountyCode": "000",
        },
        {
            "disasterNumber": 1,
            "femaDeclarationString": "EM-1-GA",
            "declarationType": "EM",
            "state": "GA",
            "incidentType": "Tropical Storm",
            "declarationTitle": "TROPICAL STORM X",
            "declarationDate": "2026-10-01T00:00:00.000Z",
            "fipsStateCode": "13",
            "fipsCountyCode": "121",
        },
    ]
    [item] = openfema.parse(rows)
    assert item.fips == ["13", "13121"]
    assert item.class_ids == ["hurricanes-tropical-storms", "disaster-recovery-rebuilding"]
    assert item.severity == 0.6


# --------------------------------------------------------------------------- CDC


def test_cdc_mmwr_dedupes_titles_and_strips_html():
    items = cdc_rss.parse("cdc_mmwr", (FIX / "cdc_mmwr.rss").read_bytes(), EPOCH)
    _contract(items)
    keys = [it.cluster_key for it in items]
    assert len(keys) == len(set(keys))
    assert all("<" not in it.title for it in items)
    assert all(it.item_type == ItemType.solution for it in items)
    # boilerplate summaries are dropped
    assert not any((it.summary or "").startswith(cdc_rss._GENERIC_SUMMARY) for it in items)


def test_cdc_outbreaks_titles_clean_and_service_type():
    items = cdc_rss.parse("cdc_outbreaks", (FIX / "cdc_outbreaks.rss").read_bytes(), EPOCH)
    _contract(items)
    assert any(it.title.startswith("Salmonella Outbreak") for it in items)
    assert all(it.item_type == ItemType.service for it in items)


def test_cdc_state_tagging_from_text():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Measles cases in West Virginia and Ohio</title><link>https://x.gov/1</link>
    <pubDate>Thu, 29 Oct 2026 17:00:00 GMT</pubDate></item>
    <item><title>National report on overdose deaths</title><link>https://x.gov/2</link>
    <pubDate>Thu, 29 Oct 2026 17:00:00 GMT</pubDate></item></channel></rss>"""
    a, b = cdc_rss.parse("cdc_mmwr", xml, EPOCH)
    assert a.fips == ["39", "54"] and not a.is_national  # no spurious Virginia
    assert b.fips == [] and b.is_national


# --------------------------------------------------------------------------- HTTP politeness


def test_polite_client_retries_then_succeeds_and_honours_retry_after():
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    client = PoliteClient(transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert client.get("https://api.weather.gov/x").json() == {"ok": True}
    assert sleeps == [7.0]
    assert calls[0].headers["User-Agent"].startswith("solbrief")


def test_polite_client_does_not_retry_client_errors():
    client = PoliteClient(transport=httpx.MockTransport(lambda r: httpx.Response(403)), sleep=lambda s: None)
    with pytest.raises(SourceError, match="403"):
        client.get("https://example.gov/blocked")


def test_registry_names_unique():
    names = [a.name for a in ADAPTERS]
    assert len(names) == len(set(names))
