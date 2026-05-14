from __future__ import annotations

from homekit.core.models import (
    Accessory,
    Characteristic,
    DiscoveredAccessory,
    Entity,
    EntityCapability,
    EntityState,
    Service,
    category_name,
)


def test_category_name_known() -> None:
    assert category_name(5) == "Lightbulb"
    assert category_name(7) == "Outlet"


def test_category_name_unknown_falls_back() -> None:
    assert category_name(999) == "Category999"


def test_characteristic_permission_helpers() -> None:
    char = Characteristic(
        aid=1,
        iid=10,
        type_uuid="00000025",
        type_name="On",
        value=False,
        format="bool",
        perms=("pr", "pw", "ev"),
    )
    assert char.readable
    assert char.writable
    assert char.event_capable


def test_service_lookup_returns_characteristic() -> None:
    char_on = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr", "pw"))
    char_bright = Characteristic(
        1, 11, "00000008", "Brightness", 50, "uint8", ("pr", "pw"), unit="percentage"
    )
    service = Service(1, 9, "00000043", "Lightbulb", (char_on, char_bright))
    assert service.get_characteristic("On") is char_on
    assert service.get_characteristic("Hue") is None


def test_accessory_get_helpers() -> None:
    char = Characteristic(1, 10, "00000025", "On", False, "bool", ("pr", "pw"))
    service = Service(1, 9, "00000043", "Lightbulb", (char,))
    accessory = Accessory(1, "AA:BB", "Lamp", (service,))
    assert accessory.get_service("Lightbulb") is service
    assert accessory.get_characteristic("On") is char
    assert accessory.get_characteristic("Missing") is None


def test_entity_capability_records_metadata() -> None:
    cap = EntityCapability(
        domain="light",
        readable=frozenset({"On", "Brightness"}),
        writable=frozenset({"On", "Brightness"}),
        units={"Brightness": "percentage"},
        safety_class="safe",
    )
    entity = Entity(
        entity_id="light.kitchen",
        domain="light",
        name="Kitchen",
        device_id="AA",
        aid=1,
        service_iid=9,
        capability=cap,
    )
    assert entity.capability.safety_class == "safe"
    assert "Brightness" in entity.capability.readable


def test_discovered_accessory_fields() -> None:
    accessory = DiscoveredAccessory(
        device_id="AA",
        name="Lamp",
        model="Eve",
        host="10.0.0.1",
        port=12345,
        category=5,
        category_name="Lightbulb",
        is_paired=False,
        config_number=1,
        is_bridge=False,
    )
    assert accessory.category_name == "Lightbulb"
    assert not accessory.is_bridge


def test_entity_state_fresh_flag() -> None:
    state = EntityState(
        entity_id="light.kitchen",
        state="on",
        attributes={"brightness": 70},
        last_seen="2026-05-14T10:00:00+00:00",
        source="event",
        fresh=True,
    )
    assert state.fresh
    assert state.source == "event"
