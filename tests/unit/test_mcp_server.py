from __future__ import annotations

from pathlib import Path

import pytest

from homekit.client import HomeKitClient
from homekit.config import load_config
from homekit.exceptions import PolicyBlockedError
from homekit.mcp_server import server
from tests.fake_backend import FakeBackend, _light_accessory, _lock_accessory


@pytest.fixture
async def homekit_service(
    tmp_homekit_config: Path, monkeypatch: pytest.MonkeyPatch
) -> server.HomeKitService:
    monkeypatch.setenv("HOMEKIT_MCP__ALLOW_WRITE_TOOLS", "true")
    config = load_config()
    backend = FakeBackend()
    backend.seed_pairing("AA:BB:CC", [_light_accessory("AA:BB:CC")])
    backend.seed_pairing("DD:EE:FF", [_lock_accessory("DD:EE:FF")])
    service = server.HomeKitService(config)
    service._client = HomeKitClient(config=config, backend=backend)
    await service.start()
    server._service = service
    yield service
    server._service = None
    await service.stop()


async def test_resource_entities_returns_light_and_lock(
    homekit_service: server.HomeKitService,
) -> None:
    entities = await server.resource_entities()
    domains = {e["domain"] for e in entities}
    assert {"light", "lock"} <= domains


async def test_tool_set_light_turns_on(homekit_service: server.HomeKitService) -> None:
    await server.resource_entities()
    payload = await server.homekit_set_light("light.kitchen_ceiling", on=True)
    assert payload["results"][0]["success"]


async def test_tool_unlock_requires_token(
    homekit_service: server.HomeKitService,
) -> None:
    await server.resource_entities()
    with pytest.raises(PolicyBlockedError):
        await server.homekit_unlock("lock.front_door", confirmation_token="")


async def test_tool_unlock_with_token_succeeds(
    homekit_service: server.HomeKitService,
) -> None:
    await server.resource_entities()
    payload = await server.homekit_unlock("lock.front_door", confirmation_token="ok")
    assert payload["success"]
