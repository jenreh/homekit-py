"""Semantic shortcuts that translate logical actions into characteristic writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homekit.exceptions import (
    AccessoryNotFoundError,
    CharacteristicNotWritableError,
)

if TYPE_CHECKING:
    from homekit.core.models import Accessory, Characteristic


@dataclass(frozen=True, slots=True)
class AliasSpec:
    service_types: tuple[str, ...]
    characteristic: str


TURN_ON_OFF = AliasSpec(("Lightbulb", "Switch", "Outlet", "Fan", "FanV2"), "On")
SET_BRIGHTNESS = AliasSpec(("Lightbulb",), "Brightness")
SET_COLOR_TEMPERATURE = AliasSpec(("Lightbulb",), "ColorTemperature")
SET_HUE = AliasSpec(("Lightbulb",), "Hue")
SET_SATURATION = AliasSpec(("Lightbulb",), "Saturation")
SET_TARGET_TEMPERATURE = AliasSpec(("Thermostat",), "TemperatureTarget")
SET_TARGET_HEATING_COOLING_STATE = AliasSpec(
    ("Thermostat",), "HeatingCoolingTarget"
)
SET_LOCK = AliasSpec(("LockMechanism",), "LockMechanismTargetState")
SET_TARGET_POSITION = AliasSpec(
    ("WindowCovering", "Window", "Door", "GarageDoorOpener"), "PositionTarget"
)
SET_TARGET_DOOR_STATE = AliasSpec(("GarageDoorOpener",), "DoorStateTarget")
SET_ROTATION_SPEED = AliasSpec(("Fan", "FanV2"), "RotationSpeed")


def kelvin_to_mirek(kelvin: int) -> int:
    """Convert a colour temperature in Kelvin to mireks (HAP unit)."""
    if kelvin <= 0:
        raise ValueError("kelvin must be positive")
    return round(1_000_000 / kelvin)


def mirek_to_kelvin(mirek: int) -> int:
    if mirek <= 0:
        raise ValueError("mirek must be positive")
    return round(1_000_000 / mirek)


def find_characteristic(
    accessory: Accessory, spec: AliasSpec
) -> Characteristic:
    """Locate the matching characteristic on ``accessory`` for ``spec``."""
    for service in accessory.services:
        if service.type_name in spec.service_types:
            char = service.get_characteristic(spec.characteristic)
            if char is not None:
                if not char.writable and spec.characteristic != "Identify":
                    raise CharacteristicNotWritableError(
                        f"{spec.characteristic} on aid={accessory.aid} "
                        f"iid={char.iid} is not writable"
                    )
                return char
    raise AccessoryNotFoundError(
        f"No characteristic {spec.characteristic} for "
        f"services {spec.service_types} on accessory {accessory.aid}"
    )


def clamp(value: float, char: Characteristic) -> float:
    """Clamp ``value`` into the characteristic's documented min/max."""
    if char.min_value is not None and value < char.min_value:
        return float(char.min_value)
    if char.max_value is not None and value > char.max_value:
        return float(char.max_value)
    return value


def coerce_bool(value: object) -> bool:
    """Accept the common truthy/falsey spellings used on the CLI."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in {"1", "true", "on", "yes", "y", "locked", "open"}:
            return True
        if normalised in {"0", "false", "off", "no", "n", "unlocked", "closed"}:
            return False
    raise ValueError(f"Cannot coerce {value!r} to bool")
