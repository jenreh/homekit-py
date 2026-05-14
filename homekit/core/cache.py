"""On-disk accessory-config cache, keyed by device-id, invalidated by ``c#``."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AccessoryCache:
    """JSON cache of accessory configurations, validated against `c#`/TTL."""

    def __init__(self, cache_dir: Path, ttl_seconds: int = 3600) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds

    def _path(self, device_id: str) -> Path:
        return self._cache_dir / device_id.upper().replace(":", "_") / "accessories.json"

    def load(self, device_id: str, config_number: int) -> list[dict[str, Any]] | None:
        """Return cached accessory dicts if `c#` matches and TTL hasn't expired."""
        path = self._path(device_id)
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Discarding corrupt cache %s: %s", path, exc)
            return None
        if not isinstance(payload, dict):
            return None
        if int(payload.get("config_number", -1)) != config_number:
            logger.debug("Cache config_number mismatch for %s", device_id)
            return None
        if (time.time() - float(payload.get("stored_at", 0))) > self._ttl:
            logger.debug("Cache TTL expired for %s", device_id)
            return None
        accessories = payload.get("accessories")
        if isinstance(accessories, list):
            return accessories
        return None

    def store(
        self,
        device_id: str,
        config_number: int,
        accessories: list[dict[str, Any]],
    ) -> None:
        """Persist the accessory configuration with `c#` + timestamp."""
        path = self._path(device_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "device_id": device_id,
            "config_number": config_number,
            "stored_at": time.time(),
            "accessories": accessories,
        }
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def invalidate(self, device_id: str) -> None:
        """Remove the cached document for a device."""
        path = self._path(device_id)
        if path.is_file():
            path.unlink()
