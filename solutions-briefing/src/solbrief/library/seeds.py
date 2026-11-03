"""Seed sources for library research: curated Community Guide findings + What Works Clearinghouse reports.

* Community Guide: hand-curated YAML (the site exposes no index without JavaScript).
* WWC: built from the official bulk export (StudyFindings → "Download"; federal data). Only interventions
  rated "Positive" or "Potentially Positive" in an outcome domain that maps cleanly onto a taxonomy class are
  kept; ratings travel with the seed so the drafter can't overstate them.
* SJN Story Tracker is NOT a seed until SJN grants permission (docs/PLAN.md §1.2).
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path

import yaml

from solbrief.taxonomy import class_ids

WWC_BASE = "https://ies.ed.gov/ncee/wwc"
WWC_EXPORT_URL = f"{WWC_BASE}/StudyFindings/AllDataExport"
WWC_POSITIVE = ("Positive Effects", "Potentially Positive Effects")

# WWC outcome domain → taxonomy class. Domains not listed are not seeded (no clean class yet).
WWC_DOMAIN_CLASSES: dict[str, str] = {
    **dict.fromkeys([
        "Alphabetics", "Comprehension", "Reading achievement", "Reading Fluency", "Literacy Achievement",
        "General Literacy Achievement", "English language arts achievement", "Writing achievement",
        "Writing Conventions", "Writing Quality", "General Mathematics Achievement", "Mathematics", "Algebra",
        "Number and Operations", "Geometry and Measurement", "Academic achievement", "Science Achievement",
    ], "literacy-learning-recovery"),
    **dict.fromkeys([
        "Cognition", "Oral language", "Communication/ Language", "Language", "Phonological processing",
        "Print knowledge", "Early reading/writing", "Social-emotional development", "Self-regulation",
    ], "early-childhood"),
    **dict.fromkeys([
        "Access and enrollment", "College Enrollment", "College Readiness", "Progressing in College",
        "Credit accumulation", "Credit accumulation and persistence", "Attainment",
        "Progressing in Developmental Education",
    ], "postsecondary-access"),
    **dict.fromkeys(["School Attendance", "Attendance (high school)"], "chronic-absenteeism"),
}  # fmt: skip


@dataclass(frozen=True)
class Seed:
    url: str
    title: str
    publisher: str
    source_type: str
    class_ids: tuple[str, ...]
    notes: dict = field(default_factory=dict, hash=False, compare=False)


def _load_yaml(name: str) -> dict:
    return yaml.safe_load(resources.files("solbrief.library").joinpath(name).read_text(encoding="utf-8"))


@cache
def all_seeds() -> tuple[Seed, ...]:
    valid = class_ids()
    out: list[Seed] = []
    for name in ("seeds_cg.yaml", "seeds_wwc.yaml"):
        try:
            doc = _load_yaml(name)
        except FileNotFoundError:
            continue
        for s in doc.get("seeds", []):
            classes = tuple(c for c in s["class_ids"] if c in valid)
            if len(classes) != len(s["class_ids"]):
                raise ValueError(f"{name}: unknown class in {s['class_ids']}")
            out.append(
                Seed(
                    s["url"],
                    s["title"],
                    s.get("publisher", doc["publisher"]),
                    s.get("source_type", doc["source_type"]),
                    classes,
                    s.get("notes", {}),
                )
            )
    return tuple(out)


def seeds_for(class_id: str) -> list[Seed]:
    return [s for s in all_seeds() if class_id in s.class_ids]


def build_wwc_seeds(export: Path) -> dict:
    """Turn the WWC bulk export (zip or CSV) into seeds_wwc.yaml content."""
    if export.suffix == ".zip":
        with zipfile.ZipFile(export) as z:
            text = z.read("Interventions_Studies_And_Findings.csv").decode("utf-8-sig", errors="replace")
    else:
        text = export.read_text(encoding="utf-8-sig", errors="replace")
    by_url: dict[str, dict] = {}
    for r in csv.DictReader(io.StringIO(text)):
        path, rating, domain = r["i_Intervention_Page_URL"], r["i_Effectiveness_Rating"], r["i_Outcome_Domain"]
        if not path or rating not in WWC_POSITIVE or domain not in WWC_DOMAIN_CLASSES:
            continue
        url = WWC_BASE + path if path.startswith("/") else path
        entry = by_url.setdefault(
            url, {"url": url, "title": r["i_Intervention_Name"].strip(), "class_ids": [], "notes": {"ratings": {}}}
        )
        cls = WWC_DOMAIN_CLASSES[domain]
        if cls not in entry["class_ids"]:
            entry["class_ids"].append(cls)
        entry["notes"]["ratings"][domain] = rating
    seeds = sorted(by_url.values(), key=lambda e: e["title"].lower())
    return {
        "publisher": "What Works Clearinghouse (IES, U.S. Department of Education)",
        "source_type": "systematic_review",
        "generated_from": WWC_EXPORT_URL,
        "seeds": seeds,
    }
