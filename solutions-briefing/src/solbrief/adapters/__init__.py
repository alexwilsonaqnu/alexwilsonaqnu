"""Problem-feed source adapters. Registry of what the ingest stage runs, with each source's lookback."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from solbrief.adapters.base import PoliteClient, RawItem, SourceAdapter, SourceError

__all__ = ["ADAPTERS", "AdapterSpec", "PoliteClient", "RawItem", "SourceAdapter", "SourceError"]


@dataclass(frozen=True)
class AdapterSpec:
    name: str
    factory: Callable[[PoliteClient], SourceAdapter]
    lookback: timedelta  # how far back each run looks (sources publish at different cadences)


def _nws(c: PoliteClient) -> SourceAdapter:
    from solbrief.adapters.nws import NWSAlertsAdapter

    return NWSAlertsAdapter(c)


def _fema(c: PoliteClient) -> SourceAdapter:
    from solbrief.adapters.openfema import OpenFEMAAdapter

    return OpenFEMAAdapter(c)


def _cdc(feed: str) -> Callable[[PoliteClient], SourceAdapter]:
    def make(c: PoliteClient) -> SourceAdapter:
        from solbrief.adapters.cdc_rss import CDCFeedAdapter

        return CDCFeedAdapter(feed, c)

    return make


ADAPTERS: tuple[AdapterSpec, ...] = (
    AdapterSpec("nws", _nws, timedelta(hours=24)),
    AdapterSpec("openfema", _fema, timedelta(days=7)),
    AdapterSpec("cdc_mmwr", _cdc("cdc_mmwr"), timedelta(days=8)),
    AdapterSpec("cdc_outbreaks", _cdc("cdc_outbreaks"), timedelta(days=30)),
)
