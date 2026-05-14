"""Detect accessories that are not currently pairable (already paired elsewhere)."""

from __future__ import annotations

from homekit.diagnostics import DiagnosticResult
from homekit.discovery import discover


async def check_pairability(timeout_s: float = 4.0) -> DiagnosticResult:
    accessories = await discover(timeout_s=timeout_s)
    if not accessories:
        return DiagnosticResult(
            "pairability", False, "No accessories visible (run `homekit diagnose mdns`)."
        )
    pairable = [a for a in accessories if not a.is_paired]
    already = [a for a in accessories if a.is_paired]
    msg_parts: list[str] = []
    if pairable:
        msg_parts.append(
            "Pairable: " + ", ".join(f"{a.name}({a.device_id})" for a in pairable[:5])
        )
    if already:
        msg_parts.append(
            "Already paired (sf=0): "
            + ", ".join(f"{a.name}({a.device_id})" for a in already[:5])
        )
    passed = bool(pairable) or bool(already)
    return DiagnosticResult(
        "pairability", passed, " | ".join(msg_parts) or "No HAP services seen"
    )
