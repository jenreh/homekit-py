"""Service → domain mapping and entity-id assignment."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from homekit.core.models import Entity, EntityCapability, SafetyClass

if TYPE_CHECKING:
    from homekit.core.models import Accessory, Service

SERVICE_DOMAIN_MAP: dict[str, str] = {
    "Lightbulb": "light",
    "Switch": "switch",
    "Outlet": "switch",
    "Thermostat": "climate",
    "TemperatureSensor": "sensor",
    "HumiditySensor": "sensor",
    "MotionSensor": "sensor",
    "ContactSensor": "sensor",
    "LightSensor": "sensor",
    "OccupancySensor": "sensor",
    "AirQualitySensor": "sensor",
    "CarbonDioxideSensor": "sensor",
    "CarbonMonoxideSensor": "sensor",
    "LeakSensor": "sensor",
    "SmokeSensor": "sensor",
    "BatteryService": "sensor",
    "LockMechanism": "lock",
    "SecuritySystem": "security_system",
    "GarageDoorOpener": "cover",
    "WindowCovering": "cover",
    "Window": "cover",
    "Door": "cover",
    "Fan": "fan",
    "FanV2": "fan",
    "AirPurifier": "fan",
    "Speaker": "media_player",
    "Television": "media_player",
    "VideoDoorbell": "doorbell",
}


SAFETY_BY_DOMAIN: dict[str, SafetyClass] = {
    "lock": "dangerous",
    "security_system": "dangerous",
    "climate": "caution",
    "cover": "caution",
    "fan": "safe",
    "light": "safe",
    "switch": "safe",
    "sensor": "safe",
    "media_player": "safe",
    "doorbell": "safe",
}


LOCK_STATE_NAMES = {0: "unsecured", 1: "secured", 2: "jammed", 3: "unknown"}
DOOR_STATE_NAMES = {
    0: "open",
    1: "closed",
    2: "opening",
    3: "closing",
    4: "stopped",
}
HEATING_COOLING_STATE_NAMES = {0: "off", 1: "heat", 2: "cool", 3: "auto"}


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_") or "device"


def _allocate_entity_id(domain: str, name: str, taken: set[str]) -> str:
    slug = _slugify(name)
    base = f"{domain}.{slug}"
    if base not in taken:
        taken.add(base)
        return base
    counter = 2
    while f"{base}_{counter}" in taken:
        counter += 1
    final = f"{base}_{counter}"
    taken.add(final)
    return final


def _build_capability(service: Service, domain: str) -> EntityCapability:
    readable: set[str] = set()
    writable: set[str] = set()
    units: dict[str, str] = {}
    enum_values: dict[str, dict[int, str]] = {}
    for char in service.characteristics:
        if char.type_name is None:
            continue
        if char.readable:
            readable.add(char.type_name)
        if char.writable:
            writable.add(char.type_name)
        if char.unit:
            units[char.type_name] = char.unit
        if char.type_name == "LockMechanismCurrentState":
            enum_values[char.type_name] = LOCK_STATE_NAMES
        elif char.type_name == "LockMechanismTargetState":
            enum_values[char.type_name] = {
                k: v for k, v in LOCK_STATE_NAMES.items() if k in (0, 1)
            }
        elif char.type_name in {"DoorStateCurrent", "DoorStateTarget"}:
            enum_values[char.type_name] = DOOR_STATE_NAMES
        elif char.type_name in {
            "HeatingCoolingCurrent",
            "HeatingCoolingTarget",
        }:
            enum_values[char.type_name] = HEATING_COOLING_STATE_NAMES
    return EntityCapability(
        domain=domain,
        readable=frozenset(readable),
        writable=frozenset(writable),
        units=units,
        enum_values=enum_values,
        safety_class=SAFETY_BY_DOMAIN.get(domain, "safe"),
    )


def _accessory_display_name(accessory: Accessory) -> str:
    info = accessory.get_service("AccessoryInformation")
    if info is not None:
        char = info.get_characteristic("Name")
        if char is not None and isinstance(char.value, str) and char.value:
            return char.value
    return accessory.name or f"accessory_{accessory.aid}"


def build_entities(
    accessories: Iterable[Accessory],
    *,
    overrides: dict[str, dict[str, object]] | None = None,
) -> list[Entity]:
    """Translate accessories/services into entities with stable entity-ids.

    Overrides may include ``entity_id`` to rename the canonical id and
    ``aliases`` to add extra resolvable names. Renames are applied after the
    default id is allocated so collisions with auto-generated ids are avoided.
    """
    overrides = overrides or {}
    taken: set[str] = set()
    entities: list[Entity] = []
    pending_renames: list[tuple[int, str]] = []
    for accessory in accessories:
        accessory_name = _accessory_display_name(accessory)
        for service in accessory.services:
            domain = SERVICE_DOMAIN_MAP.get(service.type_name or "")
            if domain is None:
                continue
            capability = _build_capability(service, domain)
            display = accessory_name
            entity_id = _allocate_entity_id(domain, display, taken)
            override = overrides.get(entity_id, {})
            aliases = tuple(str(a) for a in override.get("aliases", ()) or ())
            rename_raw = override.get("entity_id")
            entity = Entity(
                entity_id=entity_id,
                domain=domain,
                name=str(override.get("name", display)),
                device_id=accessory.device_id,
                aid=accessory.aid,
                service_iid=service.iid,
                capability=capability,
                room=override.get("room") if isinstance(override.get("room"), str) else None,
                aliases=aliases,
            )
            entities.append(entity)
            if isinstance(rename_raw, str) and rename_raw:
                pending_renames.append((len(entities) - 1, rename_raw))
    return _apply_renames(entities, pending_renames, taken)


def _apply_renames(
    entities: list[Entity],
    pending: list[tuple[int, str]],
    taken: set[str],
) -> list[Entity]:
    """Replace canonical ``entity_id`` from override; preserve old id as alias."""
    for idx, target in pending:
        current = entities[idx]
        if target == current.entity_id:
            continue
        if target in taken:
            continue
        taken.discard(current.entity_id)
        taken.add(target)
        previous_alias = (current.entity_id,)
        entities[idx] = Entity(
            entity_id=target,
            domain=current.domain,
            name=current.name,
            device_id=current.device_id,
            aid=current.aid,
            service_iid=current.service_iid,
            capability=current.capability,
            room=current.room,
            aliases=current.aliases + previous_alias,
        )
    return entities


def load_entity_overrides(path: Path) -> dict[str, dict[str, object]]:
    """Read ``entities.toml`` if present and return per-entity overrides."""
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    raw = data.get("entities") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
