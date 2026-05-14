"""Frozen value types for accessories, entities, events, and write results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SafetyClass = Literal["safe", "caution", "dangerous"]


HAP_CATEGORY_NAMES: dict[int, str] = {
    1: "Other",
    2: "Bridge",
    3: "Fan",
    4: "GarageDoorOpener",
    5: "Lightbulb",
    6: "DoorLock",
    7: "Outlet",
    8: "Switch",
    9: "Thermostat",
    10: "Sensor",
    11: "SecuritySystem",
    12: "Door",
    13: "Window",
    14: "WindowCovering",
    15: "ProgrammableSwitch",
    16: "RangeExtender",
    17: "IPCamera",
    18: "VideoDoorbell",
    19: "AirPurifier",
    20: "Heater",
    21: "AirConditioner",
    22: "Humidifier",
    23: "Dehumidifier",
    26: "Speaker",
    27: "Airport",
    28: "Sprinkler",
    29: "Faucet",
    30: "ShowerHead",
    31: "Television",
    32: "TargetController",
}


def category_name(ci: int) -> str:
    """Return the HAP category display name for a numeric `ci` TXT value."""
    return HAP_CATEGORY_NAMES.get(ci, f"Category{ci}")


@dataclass(frozen=True, slots=True)
class DiscoveredAccessory:
    """A HomeKit accessory advertised via mDNS — not necessarily paired yet."""

    device_id: str
    name: str
    model: str | None
    host: str
    port: int
    category: int
    category_name: str
    is_paired: bool
    config_number: int
    is_bridge: bool


@dataclass(frozen=True, slots=True)
class Characteristic:
    aid: int
    iid: int
    type_uuid: str
    type_name: str | None
    value: bool | int | float | str | None
    format: str
    perms: tuple[str, ...]
    unit: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    min_step: float | None = None

    @property
    def readable(self) -> bool:
        return "pr" in self.perms

    @property
    def writable(self) -> bool:
        return "pw" in self.perms

    @property
    def event_capable(self) -> bool:
        return "ev" in self.perms


@dataclass(frozen=True, slots=True)
class Service:
    aid: int
    iid: int
    type_uuid: str
    type_name: str | None
    characteristics: tuple[Characteristic, ...]
    is_primary: bool = False

    def get_characteristic(self, type_name: str) -> Characteristic | None:
        for char in self.characteristics:
            if char.type_name == type_name:
                return char
        return None


@dataclass(frozen=True, slots=True)
class Accessory:
    aid: int
    device_id: str
    name: str
    services: tuple[Service, ...]

    def get_service(self, type_name: str) -> Service | None:
        for svc in self.services:
            if svc.type_name == type_name:
                return svc
        return None

    def get_characteristic(self, type_name: str) -> Characteristic | None:
        for svc in self.services:
            char = svc.get_characteristic(type_name)
            if char is not None:
                return char
        return None


@dataclass(frozen=True, slots=True)
class AccessoryPairing:
    device_id: str
    host: str
    port: int
    name: str
    paired_at: str


@dataclass(frozen=True, slots=True)
class CharacteristicWriteResult:
    aid: int
    iid: int
    success: bool
    status: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HapEvent:
    device_id: str
    aid: int
    iid: int
    characteristic_type: str | None
    value: bool | int | float | str | None
    timestamp: str


@dataclass(frozen=True, slots=True)
class EntityCapability:
    """What an entity can read/write and how dangerous a write is."""

    domain: str
    readable: frozenset[str]
    writable: frozenset[str]
    units: dict[str, str] = field(default_factory=dict)
    enum_values: dict[str, dict[int, str]] = field(default_factory=dict)
    safety_class: SafetyClass = "safe"


@dataclass(frozen=True, slots=True)
class Entity:
    entity_id: str
    domain: str
    name: str
    device_id: str
    aid: int
    service_iid: int
    capability: EntityCapability
    room: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntityState:
    entity_id: str
    state: str
    attributes: dict[str, bool | int | float | str | None]
    last_seen: str
    source: Literal["event", "poll", "cache"]
    fresh: bool
