"""mDNS-visibility diagnostic — does anything advertise `_hap._tcp.local.`?"""

from __future__ import annotations

import asyncio

from homekit.diagnostics import DiagnosticResult
from homekit.discovery import discover


async def check_mdns(timeout_s: float = 4.0) -> DiagnosticResult:
    try:
        accessories = await asyncio.wait_for(
            discover(timeout_s=timeout_s), timeout=timeout_s + 2
        )
    except TimeoutError as exc:
        return DiagnosticResult("mdns", False, f"discovery timed out: {exc}")
    if not accessories:
        return DiagnosticResult(
            "mdns",
            False,
            "No `_hap._tcp.local.` services advertised. Check VLAN, mDNS Reflector, IGMP snooping.",
        )
    summary = ", ".join(f"{a.name}({a.device_id})" for a in accessories[:5])
    return DiagnosticResult(
        "mdns",
        True,
        f"Found {len(accessories)} accessory/accessories: {summary}",
    )
