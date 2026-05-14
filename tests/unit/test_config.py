from __future__ import annotations

from pathlib import Path

import pytest

from homekit.config import HomeKitConfig, load_config


def test_load_config_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOMEKIT_CONFIG_DIR", str(tmp_path / "config"))
    config = load_config()
    assert config.config_dir == tmp_path / "config"
    assert config.pairing_dir == tmp_path / "config" / "pairings"
    assert config.connection.mode == "ondemand"
    assert config.dangerous_operations["garage.open"] == "disabled"


def test_load_config_reads_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        "\n".join(
            [
                "[connection]",
                'mode = "persistent"',
                "request_timeout_s = 5",
                "",
                "[dangerous_operations]",
                '"cover.open" = "confirmation_required"',
                "",
            ]
        )
    )
    monkeypatch.setenv("HOMEKIT_CONFIG_DIR", str(config_dir))
    config = load_config()
    assert config.connection.mode == "persistent"
    assert config.connection.request_timeout_s == 5
    assert config.dangerous_operations["cover.open"] == "confirmation_required"


def test_env_override_takes_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        "[mcp]\nallow_write_tools = false\n"
    )
    monkeypatch.setenv("HOMEKIT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("HOMEKIT_MCP__ALLOW_WRITE_TOOLS", "true")
    config = load_config()
    assert config.mcp.allow_write_tools is True


def test_pairing_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    pairing_dir = tmp_path / "alt-pairings"
    monkeypatch.setenv("HOMEKIT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("HOMEKIT_PAIRING_DIR", str(pairing_dir))
    config = load_config()
    assert config.pairing_dir == pairing_dir


def test_home_kit_config_constructable_without_paths() -> None:
    cfg = HomeKitConfig()
    assert cfg.discovery.mdns_timeout_s == 5.0
