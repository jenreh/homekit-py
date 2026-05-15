"""Network interface diagnostic — IPv4/IPv6, multicast, Thread border router."""

from __future__ import annotations

import ipaddress
import socket

from homekit.diagnostics import DiagnosticResult


def _has_thread_ipv6() -> bool:
    """Return True if a ULA (fc00::/7) or link-local IPv6 address is present.

    A Thread Border Router advertises routes into the Thread mesh via ULA or
    site-local prefixes.  Having such an address strongly suggests the host
    can reach Thread devices directly.
    """
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET6):
            addr_str = info[4][0].split("%")[0]  # strip scope-id
            try:
                parsed = ipaddress.IPv6Address(addr_str)
            except ValueError:
                continue
            if parsed.is_private or parsed.is_link_local:
                return True
    except OSError:
        pass
    return False


def check_network() -> DiagnosticResult:
    interfaces: list[str] = []
    has_ipv4 = False
    has_thread_ipv6 = _has_thread_ipv6()
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
    thread_note = (
        " | Thread/IPv6 border-router reachable"
        if has_thread_ipv6
        else " | No Thread/IPv6 route (HomePod Mini or Apple TV 4K needed for Thread)"
    )
    return DiagnosticResult("network", True, f"Local addresses: {sample}{thread_note}")
