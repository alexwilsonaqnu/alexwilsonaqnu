"""Versioned problem taxonomy.

`vN.yaml` files are frozen snapshots. The database (`problem_classes`) is the runtime source of truth;
v1 is seeded by migration 0002 as the baseline. After that every change is a `taxonomy_changes` row:
agents may only `propose` (enforced by a DB CHECK), humans accept/reject via the admin API.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources

import yaml


@dataclass(frozen=True)
class TaxonomyClass:
    id: str
    label: str
    parent_id: str | None


@cache
def load_snapshot(version: str = "v1") -> tuple[str, tuple[TaxonomyClass, ...]]:
    """Return (note, classes) from a frozen snapshot file, parents before children."""
    text = resources.files("solbrief.taxonomy").joinpath(f"{version}.yaml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    if doc.get("version") != version:
        raise ValueError(f"{version}.yaml declares version {doc.get('version')!r}")
    out: list[TaxonomyClass] = []
    seen: set[str] = set()
    for top in doc["classes"]:
        for cls in [top, *[{**c, "_parent": top["id"]} for c in top.get("children", [])]]:
            if cls["id"] in seen:
                raise ValueError(f"duplicate class id {cls['id']!r}")
            seen.add(cls["id"])
            out.append(TaxonomyClass(id=cls["id"], label=cls["label"], parent_id=cls.get("_parent")))
    return doc.get("note", ""), tuple(out)


def class_ids(version: str = "v1") -> frozenset[str]:
    return frozenset(c.id for c in load_snapshot(version)[1])


def parent_of(class_id: str, version: str = "v1") -> str | None:
    for c in load_snapshot(version)[1]:
        if c.id == class_id:
            return c.parent_id
    raise KeyError(class_id)
