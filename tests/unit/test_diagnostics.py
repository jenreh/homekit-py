from __future__ import annotations

from pathlib import Path

import pytest

from homekit.config import load_config
from homekit.diagnostics.mcp_security import check_mcp_security
from homekit.diagnostics.storage import check_storage


def test_storage_diag_reports_missing_dir(tmp_homekit_config: Path) -> None:
    config = load_config()
    result = check_storage(config)
    assert result.passed


def test_mcp_security_passes_on_defaults(tmp_homekit_config: Path) -> None:
    config = load_config()
    result = check_mcp_security(config)
    assert result.passed


def test_mcp_security_flags_writes_without_audit(
    tmp_homekit_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOMEKIT_MCP__ALLOW_WRITE_TOOLS", "true")
    monkeypatch.setenv("HOMEKIT_MCP__AUDIT_LOG", "false")
    config = load_config()
    result = check_mcp_security(config)
    assert not result.passed
    assert "audit_log" in result.details
