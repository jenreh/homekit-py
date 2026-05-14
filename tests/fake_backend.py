"""In-memory backend used by the unit tests for client/CLI/MCP coverage."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

from homekit.core.models import (
    Accessory,
    AccessoryPairing,
    Characteristic,
    CharacteristicWriteResult,
    DiscoveredAccessory,
    HapEvent,
    Service,
)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _light_accessory(device_id: str, name: str = "Kitchen Ceiling") -> Accessory:
    info = Service(
        1,
        1,
        "0000003E",
        "AccessoryInformation",
        (
            Characteristic(1, 2, "00000023", "Name", name, "string", ("pr",)),
            Characteristic(1, 3, "00000020", "Manufacturer", "FakeCo", "string", ("pr",)),
        ),
    )
    on = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr", "pw", "ev"))
    brightness = Characteristic(
        1,
        11,
        "00000008",
        "Brightness",
        50,
        "uint8",
        ("pr", "pw"),
        unit="percentage",
        min_value=0,
        max_value=100,
        min_step=1,
    )
    color_temp = Characteristic(
        1,
        12,
        "000000CE",
        "ColorTemperature",
        300,
        "uint32",
        ("pr", "pw"),
        min_value=140,
        max_value=500,
    )
    light = Service(
        1,
        9,
        "00000043",
        "Lightbulb",
        (on, brightness, color_temp),
        is_primary=True,
    )
    return Accessory(1, device_id, name, (info, light))


def _lock_accessory(device_id: str, name: str = "Front Door") -> Accessory:
    info = Service(
        1,
        1,
        "0000003E",
        "AccessoryInformation",
        (
            Characteristic(1, 2, "00000023", "Name", name, "string", ("pr",)),
        ),
    )
    current = Characteristic(
        1, 20, "0000001D", "LockCurrentState", 1, "uint8", ("pr", "ev")
    )
    target = Characteristic(
        1, 21, "0000001E", "LockTargetState", 1, "uint8", ("pr", "pw")
    )
    lock = Service(1, 19, "00000045", "LockMechanism", (current, target), is_primary=True)
    return Accessory(1, device_id, name, (info, lock))


class FakeBackend:
    """Implements ``HomeKitBackend`` in-memory; perfect for unit tests."""

    def __init__(self) -> None:
        self._pairings: dict[str, AccessoryPairing] = {}
        self._accessories: dict[str, list[Accessory]] = {}
        self._discoverable: list[DiscoveredAccessory] = []
        self._values: dict[tuple[str, int, int], Any] = {}
        self._event_queues: dict[str, list[asyncio.Queue[HapEvent]]] = {}

    def seed_discoverable(self, accessories: list[DiscoveredAccessory]) -> None:
        self._discoverable = list(accessories)

    def seed_pairing(self, device_id: str, accessories: list[Accessory]) -> None:
        device = device_id.upper()
        self._pairings[device] = AccessoryPairing(
            device_id=device,
            host="10.0.0.1",
            port=12345,
            name=device,
            paired_at=_now(),
        )
        self._accessories[device] = accessories
        for accessory in accessories:
            for service in accessory.services:
                for char in service.characteristics:
                    self._values[(device, char.aid, char.iid)] = char.value

    async def start(self) -> None:  # noqa: D401
        return None

    async def stop(self) -> None:
        return None

    async def discover(self, timeout_s: float = 5.0) -> list[DiscoveredAccessory]:  # noqa: ARG002
        return list(self._discoverable)

    async def pair(self, device_id: str, pin: str, alias: str) -> AccessoryPairing:  # noqa: ARG002
        device = device_id.upper()
        pairing = AccessoryPairing(
            device_id=device,
            host="10.0.0.1",
            port=12345,
            name=alias,
            paired_at=_now(),
        )
        self._pairings[device] = pairing
        return pairing

    async def unpair(self, device_id: str) -> None:
        self._pairings.pop(device_id.upper(), None)
        self._accessories.pop(device_id.upper(), None)

    async def list_pairings(self) -> list[AccessoryPairing]:
        return list(self._pairings.values())

    async def list_accessories(
        self, device_id: str, *, refresh: bool = False  # noqa: ARG002
    ) -> list[Accessory]:
        return list(self._accessories.get(device_id.upper(), []))

    async def read_characteristic(
        self, device_id: str, aid: int, iid: int
    ) -> Characteristic:
        device = device_id.upper()
        for accessory in self._accessories.get(device, []):
            if accessory.aid != aid:
                continue
            for service in accessory.services:
                for char in service.characteristics:
                    if char.iid == iid:
                        current = self._values.get((device, aid, iid), char.value)
                        return Characteristic(
                            aid=char.aid,
                            iid=char.iid,
                            type_uuid=char.type_uuid,
                            type_name=char.type_name,
                            value=current,
                            format=char.format,
                            perms=char.perms,
                            unit=char.unit,
                            min_value=char.min_value,
                            max_value=char.max_value,
                            min_step=char.min_step,
                        )
        raise KeyError((aid, iid))

    async def write_characteristic(
        self, device_id: str, aid: int, iid: int, value: Any
    ) -> CharacteristicWriteResult:
        device = device_id.upper()
        self._values[(device, aid, iid)] = value
        event = HapEvent(
            device_id=device,
            aid=aid,
            iid=iid,
            characteristic_type=None,
            value=value,
            timestamp=_now(),
        )
        for queue in self._event_queues.get(device, []):
            queue.put_nowait(event)
        return CharacteristicWriteResult(aid=aid, iid=iid, success=True)

    async def identify(self, device_id: str) -> None:
        _ = device_id

    async def subscribe(
        self, device_id: str, points: list[tuple[int, int]]
    ) -> AsyncIterator[HapEvent]:
        _ = points
        queue: asyncio.Queue[HapEvent] = asyncio.Queue()
        self._event_queues.setdefault(device_id.upper(), []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._event_queues[device_id.upper()].remove(queue)


__all__ = ["FakeBackend", "_light_accessory", "_lock_accessory"]
