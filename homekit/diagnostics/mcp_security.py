"""MCP configuration sanity checks: bind-host, write tools, audit log."""

from __future__ import annotations

from homekit.config import HomeKitConfig
from homekit.diagnostics import DiagnosticResult


def check_mcp_security(config: HomeKitConfig) -> DiagnosticResult:
    findings: list[str] = []
    mcp = config.mcp
    if mcp.bind_host == "0.0.0.0":  # noqa: S104 - the literal we're warning about
        findings.append("bind_host=0.0.0.0 (refuse: bind to 127.0.0.1)")
    if mcp.allow_write_tools and not mcp.audit_log:
        findings.append("audit_log=false while write tools are allowed")
    if mcp.allow_raw_characteristic_writes and mcp.default_mode == "read_only":
        findings.append(
            "allow_raw_characteristic_writes=true but default_mode=read_only"
        )
    if findings:
        return DiagnosticResult("mcp-security", False, "; ".join(findings))
    return DiagnosticResult(
        "mcp-security",
        True,
        (
            f"mode={mcp.default_mode} writes={mcp.allow_write_tools} "
            f"bind={mcp.bind_host} audit={mcp.audit_log}"
        ),
    )
