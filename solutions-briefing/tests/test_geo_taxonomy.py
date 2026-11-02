import pytest
from sqlalchemy import text

from solbrief import geo
from solbrief.adapters.openfema import incident_classes
from solbrief.db import get_engine
from solbrief.taxonomy import class_ids, load_snapshot, parent_of


@pytest.mark.parametrize(
    "code,expected",
    [
        ("017031", "17031"),  # NWS SAME → Cook County, IL
        ("17031", "17031"),
        ("17", "17"),
        ("17000", "17"),  # whole-state code
        ("09001", "09001"),  # legacy CT county still recognised
        ("09110", "09110"),  # CT planning region (2022+)
        ("99999", None),
        ("077123", None),  # marine-style code
        ("abc", None),
    ],
)
def test_normalize_fips(code, expected):
    assert geo.normalize_fips(code) == expected


def test_county_reference_loaded():
    cs = geo.counties()
    assert len(cs) > 3200
    assert cs["17031"].name == "Cook County" and cs["17031"].state_abbr == "IL"
    assert cs["09001"].legacy and not cs["09110"].legacy


def test_counties_mentioned_exact_gazetteer_match():
    t = "Severe Group A Streptococcus Infections Among Persons Experiencing Homelessness - Missoula County, Montana"
    assert geo.counties_mentioned(t) == ["30063"]
    assert geo.counties_mentioned("Orleans Parish, Louisiana and Cook County, Illinois") == ["17031", "22071"]
    assert geo.counties_mentioned("Narnia County, Illinois") == []  # not a real county
    assert geo.counties_mentioned("Cook County, Montana") == []  # wrong state


def test_places_mentioned_prefers_county_over_its_state():
    assert geo.places_mentioned("An outbreak in Missoula County, Montana and in Idaho") == ["16", "30063"]


def test_states_mentioned_avoids_substring_states():
    assert geo.states_mentioned("Flooding in Arkansas and West Virginia") == ["05", "54"]


def test_fema_title_fallback_when_incident_is_other():
    assert incident_classes("Other", "TYPHOON CHAMPI") == ("hurricanes-tropical-storms",)
    assert incident_classes("Other", "SEVERE STORMS AND FLOODING") == ("tornadoes-severe-storms",)
    assert incident_classes("Other", "SPECIAL EVENT") == ()


def test_taxonomy_snapshot_is_well_formed():
    _, classes = load_snapshot("v1")
    ids = [c.id for c in classes]
    assert len(ids) == len(set(ids))
    assert parent_of("flooding") == "extreme-weather" and parent_of("extreme-weather") is None
    for c in classes:  # parents precede children
        if c.parent_id:
            assert ids.index(c.parent_id) < ids.index(c.id)


def test_taxonomy_seeded_by_migration():
    with get_engine().connect() as conn:
        db_ids = set(conn.execute(text("SELECT id FROM problem_classes WHERE status='active'")).scalars())
        version = conn.execute(text("SELECT version FROM taxonomy_versions")).scalar()
    assert db_ids == set(class_ids()) and version == "v1"
