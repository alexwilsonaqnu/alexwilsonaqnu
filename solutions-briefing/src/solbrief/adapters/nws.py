"""National Weather Service active alerts (api.weather.gov) — public domain.

* Geotagging: `geocode.SAME` codes are 0 + state FIPS + county FIPS → county FIPS directly.
* Classification: deterministic, from the NWS event name. Marine-only and vague products are skipped.
* Clustering: one problem per (event type, issuing office) per day; updates of an alert join it.
* Item type: `service` — acute events where protective guidance from the agency applies.
"""

from __future__ import annotations

from datetime import datetime

from solbrief.adapters.base import PoliteClient, RawItem
from solbrief.db.models import ItemType
from solbrief.geo import normalize_fips

URL = "https://api.weather.gov/alerts/active"

# Exact event names that are not land problems for a newsroom briefing.
SKIP_EVENTS = frozenset({
    "Small Craft Advisory", "Gale Warning", "Gale Watch", "Storm Warning", "Storm Watch",
    "Hurricane Force Wind Warning", "Hurricane Force Wind Watch", "Hazardous Seas Warning",
    "Hazardous Seas Watch", "Special Marine Warning", "Marine Weather Statement", "Brisk Wind Advisory",
    "Low Water Advisory", "Heavy Freezing Spray Warning", "Freezing Spray Advisory", "Dense Fog Advisory",
    "Special Weather Statement", "Hazardous Weather Outlook", "Short Term Forecast", "Test",
    "Administrative Message", "Air Stagnation Advisory",
})  # fmt: skip

# (substring of event name, class ids). First match wins; order specific → general.
EVENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Coastal Flood", ("flooding", "coastal-hazards")),
    ("Lakeshore Flood", ("flooding",)),
    ("Flash Flood", ("flooding",)),
    ("Flood", ("flooding",)),
    ("Hydrologic", ("flooding",)),
    ("Storm Surge", ("hurricanes-tropical-storms", "coastal-hazards")),
    ("Hurricane", ("hurricanes-tropical-storms",)),
    ("Tropical Storm", ("hurricanes-tropical-storms",)),
    ("Typhoon", ("hurricanes-tropical-storms",)),
    ("Tornado", ("tornadoes-severe-storms",)),
    ("Severe Thunderstorm", ("tornadoes-severe-storms",)),
    ("Extreme Wind", ("tornadoes-severe-storms",)),
    ("Heat", ("extreme-heat",)),
    ("Blizzard", ("extreme-cold-winter-storms",)),
    ("Winter", ("extreme-cold-winter-storms",)),
    ("Ice Storm", ("extreme-cold-winter-storms",)),
    ("Lake Effect Snow", ("extreme-cold-winter-storms",)),
    ("Wind Chill", ("extreme-cold-winter-storms",)),
    ("Extreme Cold", ("extreme-cold-winter-storms",)),
    ("Cold Weather", ("extreme-cold-winter-storms",)),
    ("Freeze", ("extreme-cold-winter-storms",)),
    ("Frost", ("extreme-cold-winter-storms",)),
    ("Red Flag", ("wildfire-smoke",)),
    ("Fire Weather", ("wildfire-smoke",)),
    ("Fire Warning", ("wildfire-smoke",)),
    ("Extreme Fire Danger", ("wildfire-smoke",)),
    ("Dense Smoke", ("wildfire-smoke", "air-quality")),
    ("Air Quality", ("air-quality",)),
    ("Ashfall", ("air-quality", "earthquakes-geologic")),
    ("Dust", ("air-quality",)),
    ("Tsunami", ("coastal-hazards", "earthquakes-geologic")),
    ("Earthquake", ("earthquakes-geologic",)),
    ("Avalanche", ("earthquakes-geologic",)),
    ("Rip Current", ("coastal-hazards",)),
    ("Beach Hazards", ("coastal-hazards",)),
    ("High Surf", ("coastal-hazards",)),
    ("Drought", ("drought",)),
    ("High Wind", ("extreme-weather",)),
    ("Wind Advisory", ("extreme-weather",)),
    ("Civil Danger", ("emergency-preparedness",)),
    ("Civil Emergency", ("emergency-preparedness",)),
    ("Evacuation", ("emergency-preparedness",)),
    ("Shelter In Place", ("emergency-preparedness",)),
    ("Law Enforcement", ("emergency-preparedness",)),
    ("Local Area Emergency", ("emergency-preparedness",)),
    ("Hazardous Materials", ("environmental-health-exposure",)),
    ("Radiological", ("environmental-health-exposure",)),
    ("Nuclear Power Plant", ("environmental-health-exposure",)),
    ("911 Telephone Outage", ("emergency-preparedness",)),
)

SEVERITY = {"Extreme": 1.0, "Severe": 0.75, "Moderate": 0.5, "Minor": 0.25}


def classify_event(event: str) -> tuple[str, ...]:
    for needle, classes in EVENT_RULES:
        if needle.lower() in event.lower():
            return classes
    return ()


def parse(payload: dict, since: datetime) -> list[RawItem]:
    items: list[RawItem] = []
    for feature in payload.get("features", []):
        p = feature.get("properties") or {}
        event = p.get("event") or ""
        if event in SKIP_EVENTS or p.get("status") != "Actual":
            continue
        classes = classify_event(event)
        if not classes:
            continue
        sent = datetime.fromisoformat(p["sent"])
        if sent < since:
            continue
        fips = sorted({f for code in (p.get("geocode") or {}).get("SAME", []) if (f := normalize_fips(code))})
        if not fips:  # marine zones or unknown codes: no land geography → not a local problem
            continue
        office = p.get("senderName") or p.get("sender") or "NWS"
        day = sent.date().isoformat()
        items.append(
            RawItem(
                source="nws",
                external_id=p.get("id") or feature.get("id", ""),
                cluster_key=f"{event}|{office}|{day}",
                title=f"{event} — {office.removeprefix('NWS ')}",
                summary=p.get("headline"),
                url=feature.get("id") or p.get("@id"),
                observed_at=sent,
                item_type=ItemType.service,
                fips=fips,
                class_ids=list(classes),
                severity=SEVERITY.get(p.get("severity", ""), None),
                raw={
                    "event": event,
                    "severity": p.get("severity"),
                    "urgency": p.get("urgency"),
                    "certainty": p.get("certainty"),
                    "areaDesc": p.get("areaDesc"),
                    "messageType": p.get("messageType"),
                    "expires": p.get("expires"),
                    "office": office,
                },
            )
        )
    return items


class NWSAlertsAdapter:
    name = "nws"

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._client = client or PoliteClient()

    def fetch(self, since: datetime) -> list[RawItem]:
        resp = self._client.get(
            URL,
            params={"status": "actual", "message_type": "alert,update"},
            headers={"Accept": "application/geo+json"},
        )
        return parse(resp.json(), since)
