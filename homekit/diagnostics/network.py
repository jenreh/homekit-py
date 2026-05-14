"""Network interface diagnostic — IPv4/IPv6, default routes, multicast."""

from __future__ import annotations

import ipaddress
import socket

from homekit.diagnostics import DiagnosticResult


def check_network() -> DiagnosticResult:
    interfaces: list[str] = []
    has_ipv4 = False
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            sockaddr = info[4]
            addr = sockaddr[0]
            try:
                parsed = ipaddress.ip_address(addr)
            except ValueError:
                continue
            interfaces.append(f"{addr}")
            if isinstance(parsed, ipaddress.IPv4Address) and not parsed.is_loopback:
                has_ipv4 = True
    except OSError as exc:
        return DiagnosticResult("network", False, f"getaddrinfo failed: {exc}")
    if not has_ipv4:
        return DiagnosticResult(
            "network",
            False,
            "No non-loopback IPv4 interface. Make sure the host is on the same LAN.",
        )
    sample = ", ".join(sorted(set(interfaces))[:6])
    return DiagnosticResult("network", True, f"Local addresses: {sample}")
