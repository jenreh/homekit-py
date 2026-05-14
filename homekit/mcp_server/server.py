"""FastMCP server that exposes HomeKit accessories as MCP tools + resources."""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from typing import Any

from fastmcp import FastMCP

from homekit.client import HomeKitClient
from homekit.config import HomeKitConfig, load_config
from homekit.diagnostics.mcp_security import check_mcp_security
from homekit.exceptions import HomeKitError, PolicyBlockedError

logger = logging.getLogger(__name__)


class HomeKitService:
    """Long-lived wrapper around ``HomeKitClient`` for the MCP server."""

    def __init__(self, config: HomeKitConfig) -> None:
        self._config = config
        self._client = HomeKitClient(config=config)

    @property
    def config(self) -> HomeKitConfig:
        return self._config

    @property
    def client(self) -> HomeKitClient:
        return self._client

    async def start(self) -> None:
        await self._client.start()

    async def stop(self) -> None:
        await self._client.stop()


_service: HomeKitService | None = None


def get_service() -> HomeKitService:
    if _service is None:
        raise RuntimeError("HomeKit service has not been initialised")
    return _service


def _audit_log_enabled() -> bool:
    return get_service().config.mcp.audit_log


def _audit(operation: str, **fields: Any) -> None:
    if _audit_log_enabled():
        logger.info("mcp.audit operation=%s fields=%r", operation, fields)


def _to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (frozenset, set)):
        return sorted(value)
    if isinstance(value, list):
        return [_to_dict(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_dict(v) for k, v in value.items()}
    return value


@asynccontextmanager
async def lifespan(_mcp: FastMCP):  # type: ignore[no-untyped-def]
    global _service
    config = load_config()
    diag = check_mcp_security(config)
    if not diag.passed:
        logger.warning("mcp-security advisory: %s", diag.details)
    service = HomeKitService(config)
    await service.start()
    _service = service
    try:
        yield {"homekit": service}
    finally:
        _service = None
        await service.stop()


mcp: FastMCP = FastMCP("homekit-local", lifespan=lifespan)


# --------------------------------------------------------------------- resources


@mcp.resource("homekit://devices")
async def resource_devices() -> list[dict[str, Any]]:
    service = get_service()
    pairings = await service.client.list_pairings()
    return [_to_dict(p) for p in pairings]


@mcp.resource("homekit://devices/{device_id}")
async def resource_device(device_id: str) -> dict[str, Any]:
    service = get_service()
    accessories = await service.client.get_accessories(device_id)
    return {"device_id": device_id, "accessories": [_to_dict(a) for a in accessories]}


@mcp.resource("homekit://entities")
async def resource_entities() -> list[dict[str, Any]]:
    service = get_service()
    entities = await service.client.list_entities()
    return [_to_dict(e) for e in entities]


@mcp.resource("homekit://entities/{entity_id}")
async def resource_entity(entity_id: str) -> dict[str, Any]:
    service = get_service()
    await service.client.list_entities()
    entity = await service.client.get_entity(entity_id)
    return _to_dict(entity)


@mcp.resource("homekit://state/{entity_id}")
async def resource_state(entity_id: str) -> dict[str, Any]:
    service = get_service()
    await service.client.list_entities()
    state = await service.client.get_state(entity_id, refresh=True)
    return _to_dict(state)


@mcp.resource("homekit://capabilities/{entity_id}")
async def resource_capabilities(entity_id: str) -> dict[str, Any]:
    service = get_service()
    await service.client.list_entities()
    entity = await service.client.get_entity(entity_id)
    return _to_dict(entity.capability)


@mcp.resource("homekit://events/recent")
async def resource_events_recent() -> list[dict[str, Any]]:
    service = get_service()
    return [_to_dict(state) for state in service.client.state_cache.all()]


# --------------------------------------------------------------------- tools (read)


@mcp.tool()
async def homekit_list_entities() -> list[dict[str, Any]]:
    """List every entity (lights, switches, sensors, locks, climate, covers, fans)."""
    service = get_service()
    return [_to_dict(e) for e in await service.client.list_entities()]


@mcp.tool()
async def homekit_get_state(entity_id: str) -> dict[str, Any]:
    """Return the current state of an entity, with freshness metadata."""
    service = get_service()
    await service.client.list_entities()
    return _to_dict(await service.client.get_state(entity_id, refresh=True))


@mcp.tool()
async def homekit_identify(device_id: str) -> dict[str, Any]:
    """Trigger the HAP identify routine (blink/beep) on an accessory."""
    service = get_service()
    await service.client.identify(device_id)
    _audit("identify", device_id=device_id)
    return {"ok": True, "device_id": device_id}


# --------------------------------------------------------------------- tools (write)


def _require_writes() -> None:
    if not get_service().config.mcp.allow_write_tools:
        raise PolicyBlockedError(
            "Write tools are disabled — set [mcp].allow_write_tools = true to enable"
        )


@mcp.tool()
async def homekit_set_light(
    entity_id: str,
    on: bool | None = None,
    brightness: int | None = None,
    color_temperature: int | None = None,
) -> dict[str, Any]:
    """Control a light: on/off, brightness (0–100), and colour temperature (Kelvin)."""
    _require_writes()
    service = get_service()
    await service.client.list_entities()
    results: list[Any] = []
    if on is not None:
        results.append(
            await (service.client.turn_on if on else service.client.turn_off)(entity_id)
        )
    if brightness is not None:
        results.append(await service.client.set_brightness(entity_id, float(brightness)))
    if color_temperature is not None:
        results.append(
            await service.client.set_color_temperature(entity_id, int(color_temperature))
        )
    _audit(
        "set_light",
        entity_id=entity_id,
        on=on,
        brightness=brightness,
        color_temperature=color_temperature,
    )
    return {"results": [_to_dict(r) for r in results]}


@mcp.tool()
async def homekit_set_switch(entity_id: str, on: bool) -> dict[str, Any]:
    """Turn a switch/outlet/fan on or off."""
    _require_writes()
    service = get_service()
    await service.client.list_entities()
    result = await (service.client.turn_on if on else service.client.turn_off)(entity_id)
    _audit("set_switch", entity_id=entity_id, on=on)
    return _to_dict(result)


@mcp.tool()
async def homekit_set_climate(
    entity_id: str,
    target_temperature: float | None = None,
    mode: int | None = None,
) -> dict[str, Any]:
    """Set thermostat target temperature (°C) and/or mode (0=off,1=heat,2=cool,3=auto)."""
    _require_writes()
    service = get_service()
    await service.client.list_entities()
    results: list[Any] = []
    if target_temperature is not None:
        results.append(
            await service.client.set_target_temperature(
                entity_id, float(target_temperature)
            )
        )
    if mode is not None:
        results.append(await service.client.set_target_mode(entity_id, int(mode)))
    _audit("set_climate", entity_id=entity_id, target_temperature=target_temperature, mode=mode)
    return {"results": [_to_dict(r) for r in results]}


@mcp.tool()
async def homekit_set_cover(entity_id: str, position: int) -> dict[str, Any]:
    """Set the target position of a cover/garage/window (0=closed … 100=open)."""
    _require_writes()
    service = get_service()
    await service.client.list_entities()
    result = await service.client.set_position(entity_id, position)
    _audit("set_cover", entity_id=entity_id, position=position)
    return _to_dict(result)


@mcp.tool()
async def homekit_lock(entity_id: str) -> dict[str, Any]:
    """Lock the given lock entity."""
    _require_writes()
    service = get_service()
    await service.client.list_entities()
    result = await service.client.set_lock(entity_id, True)
    _audit("lock", entity_id=entity_id)
    return _to_dict(result)


@mcp.tool()
async def homekit_unlock(
    entity_id: str, confirmation_token: str
) -> dict[str, Any]:
    """Unlock a lock — requires `confirmation_token` per the dangerous-operations policy."""
    _require_writes()
    service = get_service()
    await service.client.list_entities()
    result = await service.client.set_lock(
        entity_id, False, confirmation_token=confirmation_token
    )
    _audit("unlock", entity_id=entity_id, confirmation_token="<provided>")  # noqa: S106
    return _to_dict(result)


# --------------------------------------------------------------------- entry point


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser("homekit-mcp")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    if args.transport != "stdio":
        host = args.host or "127.0.0.1"
        if host == "0.0.0.0":  # noqa: S104
            err_msg = "Refusing to bind 0.0.0.0 — use 127.0.0.1 and front with a reverse proxy"
            raise SystemExit(err_msg)
        kwargs: dict[str, Any] = {"host": host}
        if args.port is not None:
            kwargs["port"] = args.port
        mcp.run(transport=args.transport, **kwargs)
        return
    try:
        mcp.run(transport="stdio")
    except HomeKitError as exc:
        logger.error("HomeKit MCP server stopped: %s", exc)
        raise SystemExit(1) from exc


__all__ = ["HomeKitService", "main", "mcp"]
