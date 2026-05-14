from __future__ import annotations

import pytest

from homekit.core.aliases import (
    SET_BRIGHTNESS,
    TURN_ON_OFF,
    clamp,
    coerce_bool,
    find_characteristic,
    kelvin_to_mirek,
    mirek_to_kelvin,
)
from homekit.core.models import Accessory, Characteristic, Service
from homekit.exceptions import (
    AccessoryNotFoundError,
    CharacteristicNotWritableError,
)


def _bulb() -> Accessory:
    on = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr", "pw"))
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
    service = Service(1, 9, "00000043", "Lightbulb", (on, brightness))
    return Accessory(1, "AA", "Lamp", (service,))


def test_kelvin_to_mirek_warm() -> None:
    assert kelvin_to_mirek(2700) == 370


def test_kelvin_to_mirek_cool() -> None:
    assert kelvin_to_mirek(6500) == 154


def test_kelvin_to_mirek_invalid() -> None:
    with pytest.raises(ValueError):
        kelvin_to_mirek(0)


def test_mirek_to_kelvin_round_trip() -> None:
    assert mirek_to_kelvin(370) == 2703


def test_find_characteristic_returns_match() -> None:
    accessory = _bulb()
    char = find_characteristic(accessory, TURN_ON_OFF)
    assert char.type_name == "On"


def test_find_characteristic_rejects_readonly() -> None:
    readonly = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr",))
    service = Service(1, 9, "00000043", "Lightbulb", (readonly,))
    accessory = Accessory(1, "AA", "Lamp", (service,))
    with pytest.raises(CharacteristicNotWritableError):
        find_characteristic(accessory, TURN_ON_OFF)


def test_find_characteristic_missing_service() -> None:
    accessory = Accessory(1, "AA", "Sensor", ())
    with pytest.raises(AccessoryNotFoundError):
        find_characteristic(accessory, SET_BRIGHTNESS)


def test_clamp_within_bounds() -> None:
    accessory = _bulb()
    brightness = accessory.services[0].characteristics[1]
    assert clamp(50.0, brightness) == 50.0
    assert clamp(-5, brightness) == 0.0
    assert clamp(150, brightness) == 100.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), (1, True), (0, False), ("on", True), ("off", False)],
)
def test_coerce_bool(value: object, expected: bool) -> None:
    assert coerce_bool(value) is expected


def test_coerce_bool_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        coerce_bool("maybe")
