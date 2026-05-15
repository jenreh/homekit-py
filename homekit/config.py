"""Configuration loaded from TOML + environment variables.

Priority: env var > config file > default. Two env vars override paths:
`HOMEKIT_CONFIG_DIR` and `HOMEKIT_PAIRING_DIR`.
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from platformdirs import user_cache_dir, user_config_dir
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

logger = logging.getLogger(__name__)


class ControllerSettings(BaseModel):
    name: str = "homekit-local"
    id: str = ""


class DiscoverySettings(BaseModel):
    mdns_timeout_s: float = 15.0
    ip_only: bool = False
    ble_enabled: bool = True
    thread_enabled: bool = True


class ConnectionSettings(BaseModel):
    mode: Literal["ondemand", "persistent"] = "ondemand"
    request_timeout_s: float = 10.0
    ble_timeout_s: float = 30.0
    event_reconnect_delay_s: float = 2.0
    event_poll_fallback_s: float = 30.0


class CacheSettings(BaseModel):
    ttl_seconds: int = 3600


class StorageSettings(BaseModel):
    backend: Literal["keyring", "file"] = "keyring"
    pairing_dir: str = ""


class McpSettings(BaseModel):
    default_mode: Literal["read_only", "read_write"] = "read_only"
    allow_write_tools: bool = False
    allow_raw_characteristic_writes: bool = False
    bind_host: str = "127.0.0.1"
    audit_log: bool = True


DangerousPolicy = Literal["allow", "confirmation_required", "disabled"]


_TOML_DATA: dict[str, object] = {}


class _TomlSettingsSource(PydanticBaseSettingsSource):
    """Inject TOML data as a settings source so env still wins over file."""

    def get_field_value(  # type: ignore[override]
        self,
        field: Any,  # noqa: ARG002 - signature mandated by base class
        field_name: str,
    ) -> tuple[Any, str, bool]:
        return _TOML_DATA.get(field_name), field_name, False

    def __call__(self) -> dict[str, object]:
        return dict(_TOML_DATA)


class HomeKitConfig(BaseSettings):
    """Aggregate configuration model."""

    model_config = SettingsConfigDict(
        env_prefix="HOMEKIT_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    controller: ControllerSettings = Field(default_factory=ControllerSettings)
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)
    connection: ConnectionSettings = Field(default_factory=ConnectionSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)
    dangerous_operations: dict[str, DangerousPolicy] = Field(
        default_factory=lambda: {
            "lock.unlock": "confirmation_required",
            "garage.open": "disabled",
            "security_system.disarm": "disabled",
            "cover.open": "allow",
        }
    )

    # Resolved paths (computed, not user-supplied via the model)
    config_dir: Path = Field(default_factory=Path)
    pairing_dir: Path = Field(default_factory=Path)
    cache_dir: Path = Field(default_factory=Path)

    @classmethod
    def settings_customise_sources(  # type: ignore[override]
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _TomlSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


def _config_dir() -> Path:
    override = os.environ.get("HOMEKIT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path(user_config_dir("homekit"))


def _pairing_dir(config_dir: Path) -> Path:
    override = os.environ.get("HOMEKIT_PAIRING_DIR")
    if override:
        return Path(override).expanduser()
    return config_dir / "pairings"


def _cache_dir() -> Path:
    return Path(user_cache_dir("homekit"))


def _load_toml(path: Path) -> Mapping[str, object]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Failed to parse config %s: %s", path, exc)
        return {}


def load_config(config_dir: Path | None = None) -> HomeKitConfig:
    """Load configuration from disk, then layer environment overrides on top."""
    base_dir = config_dir or _config_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    data = dict(_load_toml(base_dir / "config.toml"))
    global _TOML_DATA
    previous, _TOML_DATA = _TOML_DATA, data
    try:
        cfg = HomeKitConfig()
    finally:
        _TOML_DATA = previous
    pairing = _pairing_dir(base_dir)
    pairing.mkdir(parents=True, exist_ok=True)
    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    return cfg.model_copy(
        update={
            "config_dir": base_dir,
            "pairing_dir": pairing,
            "cache_dir": cache,
        }
    )
