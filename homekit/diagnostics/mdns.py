"""mDNS-visibility diagnostic — checks _hap._tcp (IP) and _hap._udp (Thread)."""

from __future__ import annotations

import asyncio

from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

from homekit.diagnostics import DiagnosticResult
from homekit.discovery import discover

HAP_UDP_SERVICE = "_hap._udp.local."


async def _browse_udp(timeout_s: float) -> list[str]:
    """Collect advertised Thread/UDP HAP service names."""
    found: list[str] = []

    def _on_change(_zc: object, _st: object, name: str, _sc: object) -> None:
        found.append(name)

    azc = AsyncZeroconf()
    try:
        browser = AsyncServiceBrowser(
            azc.zeroconf, HAP_UDP_SERVICE, handlers=[_on_change]
        )
        await asyncio.sleep(timeout_s)
        await browser.async_cancel()
    finally:
        await azc.async_close()
    return found


async def check_mdns(timeout_s: float = 4.0) -> DiagnosticResult:
    try:
        tcp_task = asyncio.create_task(
            asyncio.wait_for(discover(timeout_s=timeout_s), timeout=timeout_s + 2)
        )
        udp_task = asyncio.create_task(_browse_udp(timeout_s))
        accessories, udp_names = await asyncio.gather(tcp_task, udp_task)
    except TimeoutError as exc:
        return DiagnosticResult("mdns", False, f"discovery timed out: {exc}")

    if not accessories and not udp_names:
        return DiagnosticResult(
            "mdns",
            False,
            "No `_hap._tcp.local.` (IP) or `_hap._udp.local.` (Thread) services found."
            " Check VLAN, mDNS Reflector, IGMP snooping, or Thread Border Router.",
        )
    parts: list[str] = []
    if accessories:
        summary = ", ".join(f"{a.name}({a.device_id})" for a in accessories[:5])
        parts.append(f"{len(accessories)} IP: {summary}")
    if udp_names:
        parts.append(f"{len(udp_names)} Thread (UDP)")
    return DiagnosticResult("mdns", True, "; ".join(parts))
