"""Common interface for problem-feed sources.

Every adapter turns one source into `RawItem`s: normalized, geotagged (FIPS), and — when the source's own
coding makes it deterministic — pre-classified. Adapters never scrape around blocks: they use documented
APIs/feeds only, identify themselves, back off on 429/5xx, and honour Retry-After.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx

from solbrief.config import get_settings
from solbrief.db.models import ItemType

log = logging.getLogger(__name__)


@dataclass
class RawItem:
    source: str  # adapter name, e.g. "nws"
    external_id: str  # source's own id for this record
    cluster_key: str  # items sharing (source, cluster_key) on a day are one problem
    title: str
    url: str | None
    observed_at: datetime  # when the source says this happened / was issued
    item_type: ItemType
    fips: list[str] = field(default_factory=list)  # 2-digit state or 5-digit county FIPS
    is_national: bool = False
    class_ids: list[str] = field(default_factory=list)  # empty → classifier decides
    severity: float | None = None  # 0..1 from the source's own coding, if any
    summary: str | None = None
    raw: dict = field(default_factory=dict)


class SourceAdapter(Protocol):
    name: str

    def fetch(self, since: datetime) -> list[RawItem]:
        """Items issued/updated at or after `since`. Must be safe to call repeatedly."""
        ...


class SourceError(RuntimeError):
    pass


class PoliteClient:
    """httpx wrapper: identifying User-Agent, timeouts, bounded retries with backoff and Retry-After."""

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 4,
        backoff_s: float = 2.0,
        sleep=time.sleep,
    ) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": get_settings().nws_user_agent},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            transport=transport,
        )
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._sleep = sleep

    def get(self, url: str, *, raise_for_status: bool = True, **kwargs) -> httpx.Response:
        """GET with retries on 429/5xx/transport errors. With raise_for_status=False, a final 4xx response is
        returned instead of raised (the verifier needs to know a source is gone, not just that it failed)."""
        delay = self._backoff_s
        last: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = self._client.get(url, **kwargs)
            except httpx.TransportError as exc:
                last = exc
            else:
                if resp.status_code < 400:
                    return resp
                if resp.status_code not in (429, 500, 502, 503, 504):
                    if not raise_for_status:
                        return resp
                    raise SourceError(f"GET {url} -> HTTP {resp.status_code}")
                last = SourceError(f"GET {url} -> HTTP {resp.status_code}")
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = max(delay, float(retry_after))
            if attempt < self._max_attempts:
                log.warning("retrying %s in %.0fs (attempt %d): %s", url, delay, attempt, last)
                self._sleep(delay)
                delay *= 2
        raise SourceError(f"GET {url} failed after {self._max_attempts} attempts: {last}")

    def close(self) -> None:
        self._client.close()
