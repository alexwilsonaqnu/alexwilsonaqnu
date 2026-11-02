"""Stage 1 — ingest: pull every problem source → normalize → geotag → classify → cluster → score → `problems`.

Idempotent per run_date: rows are upserted on (cluster_id, run_date), and cluster_id is a stable UUID5 of
(source, cluster_key), so re-running a day rewrites the same rows and a recurring problem keeps its id
across days (which is what novelty is computed from).

One failing source never blocks the others: its error is recorded and the stage reports `partial`.
Cross-source clustering (e.g. an NWS hurricane warning and the FEMA declaration for the same storm) needs
embeddings and arrives with the matcher (M4).
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import logfire
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from solbrief import adapters
from solbrief.adapters.base import PoliteClient, RawItem
from solbrief.classify import anthropic_classifier, classify_items
from solbrief.db import session_scope
from solbrief.db.models import ItemType, Problem, RunStatus
from solbrief.pipeline.stages import StageContext, StageResult, stage

log = logging.getLogger(__name__)

CLUSTER_NAMESPACE = uuid.UUID("6f1c2d7e-1b8a-4c55-9e0c-5a1b7f3e2d10")  # fixed forever: cluster ids depend on it
STATEWIDE_COUNTY_EQUIVALENT = 20  # a statewide designation counts like ~20 counties for locality


@dataclass
class Cluster:
    source: str
    key: str
    items: list[RawItem] = field(default_factory=list)

    @property
    def cluster_id(self) -> uuid.UUID:
        return uuid.uuid5(CLUSTER_NAMESPACE, f"{self.source}|{self.key}")


def cluster_items(items: list[RawItem]) -> list[Cluster]:
    clusters: dict[tuple[str, str], Cluster] = {}
    for it in items:
        clusters.setdefault((it.source, it.cluster_key), Cluster(it.source, it.cluster_key)).items.append(it)
    return list(clusters.values())


def locality_score(fips: list[str], is_national: bool) -> float:
    """1.0 for a single county, falling as the footprint grows; 0.0 for national items."""
    if is_national or not fips:
        return 0.0
    footprint = sum(STATEWIDE_COUNTY_EQUIVALENT if len(f) == 2 else 1 for f in fips)
    return round(1.0 / (1.0 + math.log2(footprint)), 4)


def novelty_score(first_seen: datetime, run_date: date) -> float:
    """1.0 on the day a problem first appears, halving every 2 days after."""
    age_days = max(0, (run_date - first_seen.astimezone(UTC).date()).days)
    return round(0.5 ** (age_days / 2), 4)


def _merge(cluster: Cluster) -> dict:
    items = sorted(cluster.items, key=lambda i: i.observed_at)
    lead = max(items, key=lambda i: (i.severity or 0, i.observed_at))
    fips = sorted({f for i in items for f in i.fips})
    classes = list(dict.fromkeys(c for i in items for c in i.class_ids))
    is_national = not fips and any(i.is_national for i in items)
    return {
        "title": lead.title,
        "summary": lead.summary,
        "problem_class_ids": classes,
        "fips": fips,
        "is_national": is_national,
        "source_adapter": cluster.source,
        "source_urls": list(dict.fromkeys(i.url for i in items if i.url)),
        "first_seen": items[0].observed_at,
        "last_seen": items[-1].observed_at,
        "severity": max((i.severity for i in items if i.severity is not None), default=None),
        "item_type": ItemType.service if any(i.item_type == ItemType.service for i in items) else ItemType.solution,
        "raw": {"items": [i.raw | {"external_id": i.external_id} for i in items][:50], "count": len(items)},
    }


def upsert_clusters(clusters: list[Cluster], run_date: date) -> int:
    with session_scope() as s:
        for c in clusters:
            row = _merge(c)
            prior_first = s.scalar(
                select(func.min(Problem.first_seen)).where(
                    Problem.cluster_id == c.cluster_id, Problem.run_date < run_date
                )
            )
            if prior_first is not None and prior_first < row["first_seen"]:
                row["first_seen"] = prior_first
            row["novelty"] = novelty_score(row["first_seen"], run_date)
            row["locality_score"] = locality_score(row["fips"], row["is_national"])
            stmt = insert(Problem).values(cluster_id=c.cluster_id, run_date=run_date, **row)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_problem_cluster_day",
                set_={k: stmt.excluded[k] for k in row},
            )
            s.execute(stmt)
    return len(clusters)


@stage("ingest")
def ingest(ctx: StageContext) -> StageResult:
    now = datetime.now(UTC)
    client = PoliteClient()
    counts: dict = {"adapters": {}}
    errors: list[dict] = []
    items: list[RawItem] = []
    try:
        for spec in adapters.ADAPTERS:
            with logfire.span("adapter {name}", name=spec.name):
                try:
                    got = spec.factory(client).fetch(now - spec.lookback)
                except Exception as exc:  # recorded per source; other sources continue
                    log.exception("adapter %s failed", spec.name)
                    errors.append({"adapter": spec.name, "type": type(exc).__name__, "msg": str(exc)[:500]})
                    counts["adapters"][spec.name] = {"status": "failed"}
                    continue
            counts["adapters"][spec.name] = {"status": "ok", "items": len(got)}
            items.extend(got)
    finally:
        client.close()

    with logfire.span("classify {n} items", n=len(items)):
        counts["classification"] = classify_items(items, llm=anthropic_classifier())
    clusters = cluster_items(items)
    counts["problems_written"] = upsert_clusters(clusters, ctx.run_date)
    counts["items"] = len(items)

    if adapters.ADAPTERS and len(errors) == len(adapters.ADAPTERS):
        status = RunStatus.failed
    elif errors:
        status = RunStatus.partial
    else:
        status = RunStatus.succeeded
    return StageResult(status=status, counts=counts, errors=errors)
