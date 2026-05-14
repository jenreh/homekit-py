"""In-memory state cache fed by HAP events and polling fallback."""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Iterable
from typing import Literal

from homekit.core.models import EntityState

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


class StateCache:
    """Last-known entity state, with freshness metadata."""

    def __init__(self, freshness_ttl_s: float = 60.0) -> None:
        self._states: dict[str, EntityState] = {}
        self._timestamps: dict[str, float] = {}
        self._ttl = freshness_ttl_s

    def update(
        self,
        entity_id: str,
        *,
        state: str,
        attributes: dict[str, bool | int | float | str | None] | None = None,
        source: Literal["event", "poll", "cache"] = "event",
    ) -> EntityState:
        attrs = dict(attributes or {})
        snapshot = EntityState(
            entity_id=entity_id,
            state=state,
            attributes=attrs,
            last_seen=_now_iso(),
            source=source,
            fresh=True,
        )
        self._states[entity_id] = snapshot
        self._timestamps[entity_id] = time.monotonic()
        return snapshot

    def get(self, entity_id: str) -> EntityState | None:
        snapshot = self._states.get(entity_id)
        if snapshot is None:
            return None
        fresh = (time.monotonic() - self._timestamps.get(entity_id, 0)) <= self._ttl
        if fresh == snapshot.fresh:
            return snapshot
        refreshed = EntityState(
            entity_id=snapshot.entity_id,
            state=snapshot.state,
            attributes=dict(snapshot.attributes),
            last_seen=snapshot.last_seen,
            source=snapshot.source,
            fresh=fresh,
        )
        self._states[entity_id] = refreshed
        return refreshed

    def all(self) -> list[EntityState]:
        return [self.get(eid) or self._states[eid] for eid in self._states]

    def known_entities(self) -> Iterable[str]:
        return list(self._states.keys())
