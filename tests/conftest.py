"""Shared fixtures for homekit-py tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_homekit_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolate ``HOMEKIT_CONFIG_DIR`` / ``HOMEKIT_PAIRING_DIR`` per test."""
    config_dir = tmp_path / "config"
    pairing_dir = tmp_path / "pairings"
    config_dir.mkdir()
    pairing_dir.mkdir()
    monkeypatch.setenv("HOMEKIT_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("HOMEKIT_PAIRING_DIR", str(pairing_dir))
    return config_dir


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("HOMEKIT_") and key not in {
            "HOMEKIT_CONFIG_DIR",
            "HOMEKIT_PAIRING_DIR",
        }:
            monkeypatch.delenv(key, raising=False)
