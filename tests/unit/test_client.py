from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from homekit.client import HomeKitClient
from homekit.config import load_config
from homekit.exceptions import PolicyBlockedError
from tests.fake_backend import FakeBackend, _light_accessory, _lock_accessory

ClientFactory = Callable[[], Awaitable[tuple[HomeKitClient, FakeBackend]]]


@pytest.fixture
def client_factory(tmp_homekit_config: Path) -> ClientFactory:  # noqa: ARG001
    async def _make() -> tuple[HomeKitClient, FakeBackend]:
        config = load_config()
        backend = FakeBackend()
        backend.seed_pairing("AA:BB:CC", [_light_accessory("AA:BB:CC")])
        backend.seed_pairing("DD:EE:FF", [_lock_accessory("DD:EE:FF")])
        client = HomeKitClient(config=config, backend=backend)
        await client.start()
        return client, backend

    return _make


async def test_list_entities_includes_light_and_lock(client_factory) -> None:
    client, _ = await client_factory()
    entities = await client.list_entities()
    domains = {e.domain for e in entities}
    assert {"light", "lock"} <= domains
    await client.stop()


async def test_turn_on_writes_through_backend(client_factory) -> None:
    client, backend = await client_factory()
    await client.list_entities()
    result = await client.turn_on("light.kitchen_ceiling")
    assert result.success
    assert backend._values[("AA:BB:CC", 1, 10)] is True
    await client.stop()


async def test_set_brightness_clamps(client_factory) -> None:
    client, backend = await client_factory()
    await client.list_entities()
    await client.set_brightness("light.kitchen_ceiling", 150)
    assert backend._values[("AA:BB:CC", 1, 11)] == 100
    await client.stop()


async def test_set_color_temperature_converts_kelvin(client_factory) -> None:
    client, backend = await client_factory()
    await client.list_entities()
    await client.set_color_temperature("light.kitchen_ceiling", 2700)
    assert backend._values[("AA:BB:CC", 1, 12)] == 370
    await client.stop()


async def test_unlock_without_token_is_blocked(client_factory) -> None:
    client, _ = await client_factory()
    await client.list_entities()
    with pytest.raises(PolicyBlockedError):
        await client.set_lock("lock.front_door", False)
    await client.stop()


async def test_unlock_with_token_passes(client_factory) -> None:
    client, backend = await client_factory()
    await client.list_entities()
    result = await client.set_lock(
        "lock.front_door", False, confirmation_token="ok"
    )
    assert result.success
    assert backend._values[("DD:EE:FF", 1, 21)] == 0
    await client.stop()


async def test_get_state_returns_on_for_light(client_factory) -> None:
    client, _ = await client_factory()
    await client.list_entities()
    await client.turn_on("light.kitchen_ceiling")
    state = await client.get_state("light.kitchen_ceiling", refresh=True)
    assert state.state == "on"


async def test_turn_off_writes_false(client_factory) -> None:
    client, backend = await client_factory()
    await client.list_entities()
    await client.turn_on("light.kitchen_ceiling")
    await client.turn_off("light.kitchen_ceiling")
    assert backend._values[("AA:BB:CC", 1, 10)] is False
    await client.stop()


async def test_set_lock_with_lock_value(client_factory) -> None:
    client, backend = await client_factory()
    await client.list_entities()
    result = await client.set_lock("lock.front_door", True)
    assert result.success
    assert backend._values[("DD:EE:FF", 1, 21)] == 1
    await client.stop()


async def test_identify_passes_through(client_factory) -> None:
    client, _ = await client_factory()
    await client.identify("AA:BB:CC")
    await client.stop()


async def test_list_pairings_returns_seeded(client_factory) -> None:
    client, _ = await client_factory()
    pairings = await client.list_pairings()
    assert {p.device_id for p in pairings} == {"AA:BB:CC", "DD:EE:FF"}
    await client.stop()


async def test_get_entity_unknown_raises(client_factory) -> None:
    from homekit.exceptions import AccessoryNotFoundError

    client, _ = await client_factory()
    await client.list_entities()
    with pytest.raises(AccessoryNotFoundError):
        await client.get_entity("light.does_not_exist")
    await client.stop()


async def test_unpair_then_pairings_empty(client_factory) -> None:
    client, _ = await client_factory()
    await client.unpair("AA:BB:CC")
    pairings = await client.list_pairings()
    assert {p.device_id for p in pairings} == {"DD:EE:FF"}
    await client.stop()


async def test_discover_returns_seeded(client_factory) -> None:
    from homekit.core.models import DiscoveredAccessory

    client, backend = await client_factory()
    backend.seed_discoverable(
        [
            DiscoveredAccessory(
                device_id="AA",
                name="Lamp",
                model="Test",
                host="10.0.0.5",
                port=12345,
                category=5,
                category_name="Lightbulb",
                is_paired=False,
                config_number=1,
                is_bridge=False,
            )
        ]
    )
    discoveries = await client.discover()
    assert [d.device_id for d in discoveries] == ["AA"]
    await client.stop()
