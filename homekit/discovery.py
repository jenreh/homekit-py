"""mDNS discovery of HomeKit accessories via `_hap._tcp.local.`."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from homekit.core.models import DiscoveredAccessory, category_name

logger = logging.getLogger(__name__)

HAP_SERVICE = "_hap._tcp.local."


def _decode(value: Any) -> str:
    """Decode a TXT-record value (bytes or str) into a Python string."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _txt_to_dict(props: dict[Any, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_key, raw_value in props.items():
        if raw_key is None or raw_value is None:
            continue
        key = _decode(raw_key)
        out[key] = _decode(raw_value)
    return out


def parse_service_info(info: AsyncServiceInfo) -> DiscoveredAccessory | None:
    """Convert a zeroconf `AsyncServiceInfo` into a `DiscoveredAccessory`."""
    if info.properties is None or not info.addresses:
        return None
    txt = _txt_to_dict(info.properties)
    device_id = txt.get("id")
    if not device_id:
        return None
    try:
        category = int(txt.get("ci", "1"))
    except ValueError:
        category = 1
    try:
        config_number = int(txt.get("c#", "0"))
    except ValueError:
        config_number = 0
    try:
        status_flags = int(txt.get("sf", "0"))
    except ValueError:
        status_flags = 0
    name = info.name.removesuffix("." + HAP_SERVICE).rstrip(".")
    parsed_addresses = info.parsed_addresses(IPVersion.V4Only)
    host = parsed_addresses[0] if parsed_addresses else ""
    return DiscoveredAccessory(
        device_id=device_id.upper(),
        name=name,
        model=txt.get("md"),
        host=host,
        port=info.port or 0,
        category=category,
        category_name=category_name(category),
        is_paired=status_flags == 0,
        config_number=config_number,
        is_bridge=category == 2,
    )


async def discover(timeout_s: float = 5.0) -> list[DiscoveredAccessory]:
    """Browse `_hap._tcp.local.` for `timeout_s` seconds and return results."""
    found: dict[str, DiscoveredAccessory] = {}
    tasks: set[asyncio.Task[None]] = set()
    aiozc = AsyncZeroconf()

    async def _resolve(name: str) -> None:
        info = AsyncServiceInfo(HAP_SERVICE, name)
        if not await info.async_request(aiozc.zeroconf, timeout=2000):
            return
        accessory = parse_service_info(info)
        if accessory is not None:
            found[accessory.device_id] = accessory
            logger.debug(
                "Discovered accessory: %s (%s) at %s:%d",
                accessory.name,
                accessory.device_id,
                accessory.host,
                accessory.port,
            )

    def _on_change(
        zeroconf: Any,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        del zeroconf, service_type
        if state_change is ServiceStateChange.Added:
            task = asyncio.create_task(_resolve(name))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    browser = AsyncServiceBrowser(
        aiozc.zeroconf, [HAP_SERVICE], handlers=[_on_change]
    )
    try:
        await asyncio.sleep(timeout_s)
    finally:
        await browser.async_cancel()
        with suppress(Exception):
            await aiozc.async_close()
    return list(found.values())


__all__ = ["discover", "parse_service_info"]
