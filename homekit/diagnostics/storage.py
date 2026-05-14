"""Pairing-store reachability — keyring + on-disk file integrity."""

from __future__ import annotations

import json

import keyring
from keyring.errors import KeyringError

from homekit.config import HomeKitConfig
from homekit.core.storage import KEYRING_KEY, KEYRING_SERVICE
from homekit.diagnostics import DiagnosticResult


def check_storage(config: HomeKitConfig) -> DiagnosticResult:
    issues: list[str] = []
    pairing_file = config.pairing_dir / "pairings.json"
    if pairing_file.exists():
        try:
            with pairing_file.open("r", encoding="utf-8") as fh:
                json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"pairings.json corrupt: {exc}")
    if config.storage.backend == "keyring":
        try:
            keyring.get_password(KEYRING_SERVICE, KEYRING_KEY)
        except KeyringError as exc:
            issues.append(f"keyring not reachable: {exc}")
    if issues:
        return DiagnosticResult("storage", False, "; ".join(issues))
    return DiagnosticResult(
        "storage",
        True,
        f"pairing_dir={config.pairing_dir} backend={config.storage.backend}",
    )
