from __future__ import annotations

from pathlib import Path

from homekit.core.models import Accessory, Characteristic, Service
from homekit.core.registry import (
    SERVICE_DOMAIN_MAP,
    build_entities,
    load_entity_overrides,
)


def _named(value: str) -> Characteristic:
    return Characteristic(
        1, 1, "00000023", "Name", value, "string", ("pr",)
    )


def _accessory(
    aid: int, name: str, service_type: str, *chars: Characteristic
) -> Accessory:
    info = Service(
        aid,
        100 + aid,
        "0000003E",
        "AccessoryInformation",
        (_named(name),),
    )
    primary = Service(aid, 9, "00000043", service_type, chars, is_primary=True)
    return Accessory(aid, "AA", name, (info, primary))


def test_build_entities_maps_lightbulb_to_light_domain() -> None:
    on = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr", "pw"))
    entities = build_entities([_accessory(1, "Kitchen Ceiling", "Lightbulb", on)])
    assert len(entities) == 1
    assert entities[0].entity_id == "light.kitchen_ceiling"
    assert entities[0].domain == "light"
    assert entities[0].capability.safety_class == "safe"
    assert "On" in entities[0].capability.writable


def test_build_entities_collision_suffix() -> None:
    on = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr", "pw"))
    accessories = [
        _accessory(1, "Bulb", "Lightbulb", on),
        _accessory(2, "Bulb", "Lightbulb", on),
    ]
    ids = sorted(e.entity_id for e in build_entities(accessories))
    assert ids == ["light.bulb", "light.bulb_2"]


def test_build_entities_applies_entity_id_rename() -> None:
    on = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr", "pw"))
    overrides = {
        "cover.eve_shutter_switch_92e7": {
            "entity_id": "cover.esszimmer",
            "name": "Esszimmer",
        }
    }
    accessories = [_accessory(1, "Eve Shutter Switch 92E7", "WindowCovering", on)]
    entities = build_entities(accessories, overrides=overrides)
    assert entities[0].entity_id == "cover.esszimmer"
    assert entities[0].name == "Esszimmer"
    assert "cover.eve_shutter_switch_92e7" in entities[0].aliases


def test_build_entities_collects_aliases() -> None:
    on = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr", "pw"))
    overrides = {
        "light.kitchen_ceiling": {"aliases": ["kueche", "kitchen"]},
    }
    entities = build_entities(
        [_accessory(1, "Kitchen Ceiling", "Lightbulb", on)],
        overrides=overrides,
    )
    assert entities[0].entity_id == "light.kitchen_ceiling"
    assert entities[0].aliases == ("kueche", "kitchen")


def test_build_entities_rename_skips_collision() -> None:
    on = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr", "pw"))
    overrides = {"light.bulb": {"entity_id": "light.bulb_2"}}
    accessories = [
        _accessory(1, "Bulb", "Lightbulb", on),
        _accessory(2, "Bulb", "Lightbulb", on),
    ]
    entities = build_entities(accessories, overrides=overrides)
    ids = sorted(e.entity_id for e in entities)
    assert ids == ["light.bulb", "light.bulb_2"]


def test_build_entities_lock_is_dangerous() -> None:
    target = Characteristic(1, 10, "0000001E", "LockMechanismTargetState", 0, "uint8", ("pr", "pw"))
    entities = build_entities([_accessory(1, "Front Door", "LockMechanism", target)])
    assert entities[0].capability.safety_class == "dangerous"
    assert entities[0].domain == "lock"


def test_build_entities_skips_unknown_services() -> None:
    char = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr", "pw"))
    accessory = Accessory(
        1, "AA", "Foo", (Service(1, 9, "DEADBEEF", "Mystery", (char,)),)
    )
    assert build_entities([accessory]) == []


def test_service_domain_map_contains_required_entries() -> None:
    for required in ("Lightbulb", "Switch", "Outlet", "Thermostat", "LockMechanism"):
        assert required in SERVICE_DOMAIN_MAP


def test_load_entity_overrides_empty(tmp_path: Path) -> None:
    assert load_entity_overrides(tmp_path / "missing.toml") == {}


def test_load_entity_overrides_parses(tmp_path: Path) -> None:
    target = tmp_path / "entities.toml"
    target.write_text(
        '[entities."light.kitchen_ceiling"]\n'
        'name = "Kitchen"\n'
        'room = "Kitchen"\n'
        'aliases = ["main light"]\n'
    )
    overrides = load_entity_overrides(target)
    assert overrides["light.kitchen_ceiling"]["room"] == "Kitchen"
    assert overrides["light.kitchen_ceiling"]["aliases"] == ["main light"]
