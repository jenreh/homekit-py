from __future__ import annotations

import time

from homekit.core.events import StateCache


def test_update_creates_fresh_state() -> None:
    cache = StateCache(freshness_ttl_s=60)
    state = cache.update("light.kitchen", state="on", attributes={"brightness": 70})
    assert state.fresh
    assert state.state == "on"
    assert state.source == "event"


def test_state_becomes_stale_after_ttl() -> None:
    cache = StateCache(freshness_ttl_s=0.05)
    cache.update("light.kitchen", state="on")
    time.sleep(0.06)
    snapshot = cache.get("light.kitchen")
    assert snapshot is not None
    assert not snapshot.fresh


def test_get_returns_none_for_unknown() -> None:
    cache = StateCache()
    assert cache.get("light.missing") is None


def test_all_returns_every_known_entity() -> None:
    cache = StateCache()
    cache.update("a", state="on")
    cache.update("b", state="off")
    assert {s.entity_id for s in cache.all()} == {"a", "b"}
