"""Server + RemoteHomeKitClient roundtrip over an actual Unix socket."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from homekit.client import HomeKitClient
from homekit.config import load_config
from homekit.daemon.client import DaemonRpcClient, RemoteHomeKitClient
from homekit.daemon.server import DaemonServer
from homekit.exceptions import AccessoryNotFoundError
from tests.fake_backend import FakeBackend, _light_accessory, _lock_accessory


def _short_socket() -> Path:
    return Path(tempfile.gettempdir()) / f"hk-{uuid.uuid4().hex[:8]}.sock"


@pytest.fixture
async def daemon_pair(
    tmp_homekit_config: Path,
) -> AsyncIterator[tuple[RemoteHomeKitClient, FakeBackend]]:
    config = load_config()
    backend = FakeBackend()
    backend.seed_pairing("AA:BB:CC", [_light_accessory("AA:BB:CC")])
    backend.seed_pairing("DD:EE:FF", [_lock_accessory("DD:EE:FF")])
    client = HomeKitClient(config=config, backend=backend)
    socket_path = _short_socket()
    server = DaemonServer(client, socket_path, idle_timeout_s=300.0)
    await server.start()
    rpc = DaemonRpcClient(socket_path)
    await rpc.connect()
    remote = RemoteHomeKitClient(rpc)
    try:
        yield remote, backend
    finally:
        await rpc.close()
        await server.stop()
        if socket_path.exists():
            socket_path.unlink()


async def test_list_entities_over_socket(daemon_pair) -> None:
    remote, _ = daemon_pair
    entities = await remote.list_entities()
    domains = {e.domain for e in entities}
    assert {"light", "lock"} <= domains
    assert all(isinstance(e.capability.readable, frozenset) for e in entities)


async def test_turn_on_round_trips_dataclass(daemon_pair) -> None:
    remote, backend = daemon_pair
    await remote.list_entities()
    result = await remote.turn_on("light.kitchen_ceiling")
    assert result.success is True
    assert backend._values[("AA:BB:CC", 1, 10)] is True


async def test_unknown_entity_raises_remote_error(daemon_pair) -> None:
    remote, _ = daemon_pair
    with pytest.raises(AccessoryNotFoundError):
        await remote.get_entity("light.does_not_exist")


async def test_get_state_returns_state_dataclass(daemon_pair) -> None:
    remote, _ = daemon_pair
    await remote.list_entities()
    state = await remote.get_state("light.kitchen_ceiling")
    assert state.state in {"on", "off"}
    assert state.source == "poll"


async def test_listen_stream_delivers_event(daemon_pair) -> None:
    remote, _ = daemon_pair
    await remote.list_entities()
    received: list = []

    async def consume() -> None:
        async for event in remote.listen(["light.kitchen_ceiling"]):
            received.append(event)
            if len(received) >= 1:
                break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await remote.turn_on("light.kitchen_ceiling")
    await asyncio.wait_for(consumer, timeout=2.0)
    assert received
    assert received[0].device_id == "AA:BB:CC"
    assert received[0].value is True


async def test_ping_and_shutdown_methods(tmp_homekit_config) -> None:
    config = load_config()
    backend = FakeBackend()
    client = HomeKitClient(config=config, backend=backend)
    socket_path = _short_socket()
    server = DaemonServer(client, socket_path, idle_timeout_s=300.0)
    await server.start()
    rpc = DaemonRpcClient(socket_path)
    await rpc.connect()
    try:
        assert await rpc.call("ping") == "pong"
        assert await rpc.call("shutdown") == "ok"
        # serve_forever should now complete
        await asyncio.wait_for(server.serve_forever(), timeout=1.0)
    finally:
        await rpc.close()
        await server.stop()


async def test_remote_setters_round_trip(daemon_pair) -> None:
    """Exercise every passthrough on RemoteHomeKitClient against FakeBackend."""
    remote, _ = daemon_pair
    await remote.list_entities()
    assert (await remote.set_brightness("light.kitchen_ceiling", 80)).success
    assert (await remote.set_color_temperature("light.kitchen_ceiling", 2700)).success
    assert (await remote.turn_off("light.kitchen_ceiling")).success
    char = await remote.get_characteristic("AA:BB:CC", 1, 10)
    assert char.type_name == "On"
    write = await remote.put_characteristic("AA:BB:CC", 1, 10, True)
    assert write.success
    accessories = await remote.get_accessories("AA:BB:CC")
    assert accessories[0].device_id == "AA:BB:CC"
    pairings = await remote.list_pairings()
    assert any(p.device_id == "AA:BB:CC" for p in pairings)
    discovered = await remote.discover(0.1)
    assert isinstance(discovered, list)
    await remote.identify("AA:BB:CC")


async def test_unknown_method_returns_protocol_error(tmp_homekit_config) -> None:
    config = load_config()
    backend = FakeBackend()
    client = HomeKitClient(config=config, backend=backend)
    socket_path = _short_socket()
    server = DaemonServer(client, socket_path, idle_timeout_s=300.0)
    await server.start()
    rpc = DaemonRpcClient(socket_path)
    await rpc.connect()
    try:
        with pytest.raises(Exception) as excinfo:
            await rpc.call("nope.method")
        assert "Unknown method" in str(excinfo.value)
    finally:
        await rpc.close()
        await server.stop()
        if socket_path.exists():
            socket_path.unlink()


async def test_concurrent_calls_serialise_per_device(daemon_pair) -> None:
    remote, backend = daemon_pair
    await remote.list_entities()
    results = await asyncio.gather(
        remote.set_brightness("light.kitchen_ceiling", 30),
        remote.set_brightness("light.kitchen_ceiling", 70),
    )
    assert all(r.success for r in results)
    assert backend._values[("AA:BB:CC", 1, 11)] in {30, 70}
