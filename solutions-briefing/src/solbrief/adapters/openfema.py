"""OpenFEMA disaster declarations (DisasterDeclarationsSummaries v2) — federal open data.

* One declaration (disasterNumber) has one row per designated area → one problem with a FIPS list.
* `fipsCountyCode == "000"` means statewide (or tribal) → the 2-digit state FIPS.
* Classification: deterministic from `incidentType`, plus disaster recovery.
* Item type: `solution` — recovery is a systemic problem with documented responses.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime

from solbrief.adapters.base import PoliteClient, RawItem
from solbrief.db.models import ItemType
from solbrief.geo import normalize_fips

URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
PAGE = 1000

INCIDENT_CLASSES: dict[str, tuple[str, ...]] = {
    "Hurricane": ("hurricanes-tropical-storms",),
    "Tropical Storm": ("hurricanes-tropical-storms",),
    "Typhoon": ("hurricanes-tropical-storms",),
    "Tropical Depression": ("hurricanes-tropical-storms",),
    "Coastal Storm": ("coastal-hazards",),
    "Flood": ("flooding",),
    "Dam/Levee Break": ("flooding", "aging-infrastructure"),
    "Severe Storm": ("tornadoes-severe-storms",),
    "Severe Storm(s)": ("tornadoes-severe-storms",),
    "Tornado": ("tornadoes-severe-storms",),
    "Fire": ("wildfire-smoke",),
    "Snowstorm": ("extreme-cold-winter-storms",),
    "Severe Ice Storm": ("extreme-cold-winter-storms",),
    "Winter Storm": ("extreme-cold-winter-storms",),
    "Freezing": ("extreme-cold-winter-storms",),
    "Earthquake": ("earthquakes-geologic",),
    "Mud/Landslide": ("earthquakes-geologic",),
    "Volcanic Eruption": ("earthquakes-geologic",),
    "Tsunami": ("coastal-hazards", "earthquakes-geologic"),
    "Drought": ("drought",),
    "Biological": ("infectious-disease-outbreaks",),
    "Chemical": ("environmental-health-exposure",),
    "Toxic Substances": ("environmental-health-exposure",),
}

# Declaration type → base severity (major disaster > emergency > fire management)
DECLARATION_SEVERITY = {"DR": 0.8, "EM": 0.6, "FM": 0.5}


def incident_classes(incident: str, title: str) -> tuple[str, ...]:
    """From incidentType; if FEMA coded it 'Other', fall back to incident words in the declaration title."""
    if incident in INCIDENT_CLASSES:
        return INCIDENT_CLASSES[incident]
    t = title.lower()
    for keyword, classes in sorted(INCIDENT_CLASSES.items(), key=lambda kv: -len(kv[0])):
        if keyword.lower().removesuffix("(s)") in t:
            return classes
    return ()


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse(rows: list[dict]) -> list[RawItem]:
    by_disaster: OrderedDict[int, list[dict]] = OrderedDict()
    for r in rows:
        by_disaster.setdefault(r["disasterNumber"], []).append(r)
    items: list[RawItem] = []
    for number, group in by_disaster.items():
        first = group[0]
        fips: set[str] = set()
        for r in group:
            county = r.get("fipsCountyCode") or "000"
            code = r["fipsStateCode"] if county == "000" else r["fipsStateCode"] + county
            if (norm := normalize_fips(code)) is not None:
                fips.add(norm)
        incident = first.get("incidentType") or "Other"
        classes = (
            *incident_classes(incident, first.get("declarationTitle") or ""),
            "disaster-recovery-rebuilding",
        )
        decl = first.get("femaDeclarationString") or f"{first.get('declarationType')}-{number}"
        title = (first.get("declarationTitle") or incident).title()
        items.append(
            RawItem(
                source="openfema",
                external_id=decl,
                cluster_key=decl,
                title=f"FEMA {decl}: {title} ({first.get('state')})",
                summary=(
                    f"{first.get('declarationType')} declaration for {incident.lower()} in {first.get('state')}, "
                    f"{len(group)} designated area(s)."
                ),
                url=f"https://www.fema.gov/disaster/{number}",
                observed_at=_parse_ts(first["declarationDate"]),
                item_type=ItemType.solution,
                fips=sorted(fips),
                class_ids=list(dict.fromkeys(classes)),
                severity=DECLARATION_SEVERITY.get(first.get("declarationType", ""), 0.5),
                raw={
                    "disasterNumber": number,
                    "declarationType": first.get("declarationType"),
                    "incidentType": incident,
                    "state": first.get("state"),
                    "incidentBeginDate": first.get("incidentBeginDate"),
                    "programs": {
                        k: first.get(k)
                        for k in (
                            "ihProgramDeclared",
                            "iaProgramDeclared",
                            "paProgramDeclared",
                            "hmProgramDeclared",
                        )
                    },
                    "designatedAreas": [r.get("designatedArea") for r in group],
                },
            )
        )
    return items


class OpenFEMAAdapter:
    name = "openfema"

    def __init__(self, client: PoliteClient | None = None) -> None:
        self._client = client or PoliteClient()

    def fetch(self, since: datetime) -> list[RawItem]:
        since_iso = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        rows: list[dict] = []
        skip = 0
        while True:
            resp = self._client.get(
                URL,
                params={
                    "$filter": f"declarationDate ge '{since_iso}'",
                    "$orderby": "declarationDate desc",
                    "$top": PAGE,
                    "$skip": skip,
                },
            )
            page = resp.json().get("DisasterDeclarationsSummaries", [])
            rows.extend(page)
            if len(page) < PAGE:
                break
            skip += PAGE
        return parse(rows)
