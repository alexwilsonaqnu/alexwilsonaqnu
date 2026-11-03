"""Pipeline stage registry.

Each stage is a plain function `(StageContext) -> StageResult`. Stages read inputs from Postgres and
write outputs with upserts keyed on run_date, so any stage can be re-run for any date.

Stages not yet built are registered stubs that report `skipped`; real stages replace them as each
milestone lands (ingest: M2; verify, library_research: M3; match: M4).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from solbrief.db.models import RunStatus


@dataclass(frozen=True)
class StageContext:
    run_date: date
    dry_run: bool


@dataclass
class StageResult:
    status: RunStatus = RunStatus.succeeded
    counts: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


StageFn = Callable[[StageContext], StageResult]

# Order matters: this is the daily pipeline from PLAN.md §4.
STAGE_ORDER: tuple[str, ...] = (
    "ingest",
    "library_research",
    "verify",
    "match",
    "write",
    "evaluate",
    "deliver",
    "run_report",
)

_REGISTRY: dict[str, StageFn] = {}


def stage(name: str) -> Callable[[StageFn], StageFn]:
    if name not in STAGE_ORDER:
        raise ValueError(f"unknown stage {name!r}")

    def deco(fn: StageFn) -> StageFn:
        _REGISTRY[name] = fn
        return fn

    return deco


def get_stage(name: str) -> StageFn:
    if name not in _REGISTRY:
        raise KeyError(f"stage {name!r} is not registered")
    return _REGISTRY[name]


def _not_implemented(milestone: str) -> StageFn:
    def fn(ctx: StageContext) -> StageResult:
        return StageResult(status=RunStatus.skipped, counts={"implemented_in": milestone})

    return fn


# Implemented stages register themselves with @stage when their module is imported (see daily.py).
for _name, _milestone in {
    "write": "M5",
    "evaluate": "M5",
    "deliver": "M5",
}.items():
    _REGISTRY[_name] = _not_implemented(_milestone)
