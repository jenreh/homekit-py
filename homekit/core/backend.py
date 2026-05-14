"""Stable internal backend interface — the only abstraction homekit consumers see.

Every other module talks to this Protocol; only ``backends.aiohomekit_backend``
imports `aiohomekit` directly. Swapping the backend (e.g. a fake for tests)
just means implementing this Protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from homekit.core.models import (
        Accessory,
        AccessoryPairing,
        Characteristic,
        CharacteristicWriteResult,
        DiscoveredAccessory,
        HapEvent,
    )


@runtime_checkable
class HomeKitBackend(Protocol):
    """The HAP backend surface used by the high-level client."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def discover(self, timeout_s: float = 5.0) -> list[DiscoveredAccessory]:
        ...

    async def pair(
        self, device_id: str, pin: str, alias: str
    ) -> AccessoryPairing: ...

    async def unpair(self, device_id: str) -> None: ...

    async def list_pairings(self) -> list[AccessoryPairing]: ...

    async def list_accessories(
        self, device_id: str, *, refresh: bool = False
    ) -> list[Accessory]: ...

    async def read_characteristic(
        self, device_id: str, aid: int, iid: int
    ) -> Characteristic: ...

    async def write_characteristic(
        self, device_id: str, aid: int, iid: int, value: Any
    ) -> CharacteristicWriteResult: ...

    async def identify(self, device_id: str) -> None: ...

    async def subscribe(
        self, device_id: str, points: list[tuple[int, int]]
    ) -> AsyncIterator[HapEvent]: ...
