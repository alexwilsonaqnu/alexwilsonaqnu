"""US geography reference: states and counties (FIPS), loaded from public-domain Census files.

`data/counties.csv` is built from the Census 2024 county gazetteer plus the 2020 county list; entries only
in the 2020 list (e.g. Connecticut's pre-2022 counties, still used by some feeds) are flagged `legacy`.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources

# State/territory FIPS → (USPS abbreviation, name)
STATES: dict[str, tuple[str, str]] = {
    "01": ("AL", "Alabama"), "02": ("AK", "Alaska"), "04": ("AZ", "Arizona"), "05": ("AR", "Arkansas"),
    "06": ("CA", "California"), "08": ("CO", "Colorado"), "09": ("CT", "Connecticut"),
    "10": ("DE", "Delaware"), "11": ("DC", "District of Columbia"), "12": ("FL", "Florida"),
    "13": ("GA", "Georgia"), "15": ("HI", "Hawaii"), "16": ("ID", "Idaho"), "17": ("IL", "Illinois"),
    "18": ("IN", "Indiana"), "19": ("IA", "Iowa"), "20": ("KS", "Kansas"), "21": ("KY", "Kentucky"),
    "22": ("LA", "Louisiana"), "23": ("ME", "Maine"), "24": ("MD", "Maryland"),
    "25": ("MA", "Massachusetts"), "26": ("MI", "Michigan"), "27": ("MN", "Minnesota"),
    "28": ("MS", "Mississippi"), "29": ("MO", "Missouri"), "30": ("MT", "Montana"),
    "31": ("NE", "Nebraska"), "32": ("NV", "Nevada"), "33": ("NH", "New Hampshire"),
    "34": ("NJ", "New Jersey"), "35": ("NM", "New Mexico"), "36": ("NY", "New York"),
    "37": ("NC", "North Carolina"), "38": ("ND", "North Dakota"), "39": ("OH", "Ohio"),
    "40": ("OK", "Oklahoma"), "41": ("OR", "Oregon"), "42": ("PA", "Pennsylvania"),
    "44": ("RI", "Rhode Island"), "45": ("SC", "South Carolina"), "46": ("SD", "South Dakota"),
    "47": ("TN", "Tennessee"), "48": ("TX", "Texas"), "49": ("UT", "Utah"), "50": ("VT", "Vermont"),
    "51": ("VA", "Virginia"), "53": ("WA", "Washington"), "54": ("WV", "West Virginia"),
    "55": ("WI", "Wisconsin"), "56": ("WY", "Wyoming"), "60": ("AS", "American Samoa"),
    "66": ("GU", "Guam"), "69": ("MP", "Northern Mariana Islands"), "72": ("PR", "Puerto Rico"),
    "78": ("VI", "U.S. Virgin Islands"),
}  # fmt: skip
ABBR_TO_FIPS = {abbr: fips for fips, (abbr, _) in STATES.items()}
NAME_TO_FIPS = {name.lower(): fips for fips, (_, name) in STATES.items()}


@dataclass(frozen=True)
class County:
    fips: str
    state_abbr: str
    name: str
    legacy: bool


@cache
def counties() -> dict[str, County]:
    text = resources.files("solbrief.geo").joinpath("data/counties.csv").read_text(encoding="utf-8")
    return {
        row["fips"]: County(row["fips"], row["state_abbr"], row["name"], row["legacy"] == "1")
        for row in csv.DictReader(text.splitlines())
    }


def is_state_fips(code: str) -> bool:
    return len(code) == 2 and code in STATES


def is_county_fips(code: str) -> bool:
    return len(code) == 5 and code in counties()


def normalize_fips(code: str) -> str | None:
    """Return a valid 2-digit state or 5-digit county FIPS, or None. Accepts NWS SAME codes (0SSCCC)."""
    code = code.strip()
    if len(code) == 6 and code.startswith("0") and code.isdigit():
        code = code[1:]
    if is_county_fips(code) or is_state_fips(code):
        return code
    if len(code) == 5 and code.isdigit() and code.endswith("000") and is_state_fips(code[:2]):
        return code[:2]  # whole-state code used by some feeds
    return None


def state_of(fips: str) -> str:
    return fips[:2]


def describe(fips: str) -> str:
    if is_state_fips(fips):
        return STATES[fips][1]
    c = counties().get(fips)
    return f"{c.name}, {c.state_abbr}" if c else fips


_STATE_ALT = "|".join(sorted((re.escape(name) for _, name in STATES.values()), key=len, reverse=True))
_STATE_NAME_RE = re.compile(r"\b(" + _STATE_ALT + r")\b", re.IGNORECASE)


_COUNTY_SUFFIX = r"(?:County|Parish|Borough|Census Area|City and Borough|Municipality|Planning Region)"
_COUNTY_RE = re.compile(r"\b((?:[A-Z][\w.'\-]*\s){1,4}" + _COUNTY_SUFFIX + r"),?\s+(" + _STATE_ALT + r")\b")


@cache
def _county_lookup() -> dict[tuple[str, str], str]:
    return {(c.fips[:2], c.name.lower()): c.fips for c in counties().values()}


def counties_mentioned(text: str) -> list[str]:
    """County FIPS for explicit 'X County, State' mentions (exact gazetteer name match only)."""
    found = set()
    lookup = _county_lookup()
    for m in _COUNTY_RE.finditer(text):
        name, state = m.group(1).strip(), m.group(2)
        state_fips = NAME_TO_FIPS.get(state.lower())
        words = name.split()
        # The greedy prefix may include extra leading words ("Among Persons in Missoula County"):
        # try the longest suffix of the phrase that is a real county name.
        for start in range(len(words)):
            candidate = " ".join(words[start:]).lower()
            if (fips := lookup.get((state_fips, candidate))) is not None:
                found.add(fips)
                break
    return sorted(found)


def places_mentioned(text: str) -> list[str]:
    """Counties named explicitly, plus states named that don't already have a county match."""
    county_fips = counties_mentioned(text)
    covered = {f[:2] for f in county_fips}
    return sorted(set(county_fips) | {s for s in states_mentioned(text) if s not in covered})


def states_mentioned(text: str) -> list[str]:
    """State FIPS for full state names mentioned in text (deterministic; no abbreviations, to avoid
    false positives like 'IN', 'OR', 'ME'). Longest names match first, so 'West Virginia' does not also
    yield Virginia. 'Washington' is read as the state (D.C. must be written 'District of Columbia')."""
    return sorted({NAME_TO_FIPS[m.group(1).lower()] for m in _STATE_NAME_RE.finditer(text)})
