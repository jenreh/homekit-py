from __future__ import annotations

import json
import time
from pathlib import Path

from homekit.core.cache import AccessoryCache


def test_store_then_load(tmp_path: Path) -> None:
    cache = AccessoryCache(tmp_path, ttl_seconds=3600)
    payload = [{"aid": 1, "services": []}]
    cache.store("AA:BB", 7, payload)
    assert cache.load("AA:BB", 7) == payload


def test_load_returns_none_on_config_number_mismatch(tmp_path: Path) -> None:
    cache = AccessoryCache(tmp_path)
    cache.store("AA", 1, [{"aid": 1}])
    assert cache.load("AA", 2) is None


def test_load_returns_none_when_ttl_expired(tmp_path: Path) -> None:
    cache = AccessoryCache(tmp_path, ttl_seconds=1)
    cache.store("AA", 1, [{"aid": 1}])
    # rewrite stored_at to past
    path = tmp_path / "AA" / "accessories.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stored_at"] = time.time() - 10
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert cache.load("AA", 1) is None


def test_invalidate_removes_file(tmp_path: Path) -> None:
    cache = AccessoryCache(tmp_path)
    cache.store("AA", 1, [{"aid": 1}])
    cache.invalidate("AA")
    assert cache.load("AA", 1) is None


def test_load_handles_corrupt_payload(tmp_path: Path) -> None:
    cache = AccessoryCache(tmp_path)
    target = tmp_path / "AA" / "accessories.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not json}", encoding="utf-8")
    assert cache.load("AA", 0) is None
