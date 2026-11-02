"""CDC RSS feeds (tools.cdc.gov content syndication): MMWR and US-based outbreaks.

The CDC Health Alert Network has no live feed (see docs/PLAN.md §1.1), so it has no adapter.

* No geocoding in the feeds: we tag explicit 'X County, State' mentions and full state names in the
  title/summary (deterministic gazetteer matches); otherwise the item is national.
* Classification is left to the classifier (keyword rules, optional LLM fallback).
* MMWR feeds carry the whole archive and duplicate titles → lookback window + title-based clustering.
* Item types: outbreaks → `service` (protective guidance); MMWR → `solution` (systemic findings).
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import feedparser

from solbrief.adapters.base import PoliteClient, RawItem
from solbrief.db.models import ItemType
from solbrief.geo import places_mentioned

FEEDS = {
    "cdc_mmwr": ("https://tools.cdc.gov/api/v2/resources/media/342778.rss", ItemType.solution),
    "cdc_outbreaks": ("https://tools.cdc.gov/api/v2/resources/media/285676.rss", ItemType.service),
}

_TAG = re.compile(r"<[^>]+>")
_GENERIC_SUMMARY = "The Morbidity and Mortality Weekly Report (MMWR) Series is prepared by"


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", text or ""))).strip()


def _cluster_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:200]


def parse(feed_name: str, xml: str | bytes, since: datetime) -> list[RawItem]:
    _, item_type = FEEDS[feed_name]
    parsed = feedparser.parse(xml)
    items: dict[str, RawItem] = {}
    for e in parsed.entries:
        published = e.get("published")
        if not published:
            continue
        when = parsedate_to_datetime(published)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if when < since:
            continue
        title = clean(e.get("title"))
        if not title:
            continue
        summary = clean(e.get("summary"))
        if summary.startswith(_GENERIC_SUMMARY) or summary == title:
            summary = ""
        key = _cluster_key(title)
        if key in items:  # same report syndicated twice
            continue
        places = places_mentioned(f"{title} {summary}")
        items[key] = RawItem(
            source=feed_name,
            external_id=e.get("id") or e.get("link") or key,
            cluster_key=key,
            title=title,
            summary=summary or None,
            url=e.get("link"),
            observed_at=when,
            item_type=item_type,
            fips=places,
            is_national=not places,
            raw={"feed": feed_name},
        )
    return list(items.values())


class CDCFeedAdapter:
    def __init__(self, feed_name: str, client: PoliteClient | None = None) -> None:
        if feed_name not in FEEDS:
            raise ValueError(feed_name)
        self.name = feed_name
        self._client = client or PoliteClient()

    def fetch(self, since: datetime) -> list[RawItem]:
        url, _ = FEEDS[self.name]
        return parse(self.name, self._client.get(url).content, since)
