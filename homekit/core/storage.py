"""Pairing persistence — keyring primary, encrypted file fallback.

The file backend stores a JSON document compatible with aiohomekit's
`load_data` / `save_data` API. The keyring backend mirrors the same document
into the OS keychain under service ``homekit-py`` / key ``pairings``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import stat
import tempfile
from pathlib import Path

import keyring
from keyring.errors import KeyringError

from homekit.exceptions import PairingStoreCorruptError

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "homekit-py"
KEYRING_KEY = "pairings"


class PairingStore:
    """Manage pairings.json on disk, optionally mirrored to OS keychain."""

    def __init__(
        self,
        pairing_dir: Path,
        backend: str = "keyring",
    ) -> None:
        self._pairing_dir = Path(pairing_dir)
        self._pairing_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._pairing_dir / "pairings.json"
        self._backend = backend

    @property
    def path(self) -> Path:
        """Absolute path to the JSON file (always materialised for aiohomekit)."""
        return self._path

    def ensure_file(self) -> None:
        """Materialise the JSON file on disk, hydrating from keyring if needed."""
        if self._backend == "keyring":
            payload = self._read_keyring()
            if payload is not None and not self._path.exists():
                self._write_atomic(payload)
        if not self._path.exists():
            self._write_atomic("{}")

    def sync_to_secure_store(self) -> None:
        """Push the on-disk JSON into the keyring if that backend is active."""
        if self._backend != "keyring":
            return
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                payload = fh.read()
        except FileNotFoundError:
            return
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, payload)
        except KeyringError as exc:
            logger.warning(
                "Keyring write failed (%s); pairing remains in %s", exc, self._path
            )

    def list_aliases(self) -> list[str]:
        """Return the aliases of pairings currently stored."""
        data = self._read_file()
        return sorted(data.keys())

    def get_alias_for_device(self, device_id: str) -> str | None:
        """Look up the alias previously assigned to a pairing."""
        data = self._read_file()
        target = device_id.upper()
        for alias, row in data.items():
            if isinstance(row, dict) and str(row.get("AccessoryPairingID", "")).upper() == target:
                return alias
        return None

    def export_dict(self) -> dict[str, object]:
        """Return the raw pairings dict for backup/export purposes."""
        return self._read_file()

    def import_dict(self, payload: dict[str, object]) -> None:
        """Replace stored pairings with the given mapping."""
        self._write_atomic(json.dumps(payload, indent=2))
        self.sync_to_secure_store()

    # ------------------------------------------------------------------ private

    def _read_file(self) -> dict[str, object]:
        self.ensure_file()
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise PairingStoreCorruptError(
                f"Pairing store at {self._path} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise PairingStoreCorruptError(
                f"Pairing store at {self._path} has unexpected shape"
            )
        return data

    def _write_atomic(self, payload: str) -> None:
        fd, tmp_name = tempfile.mkstemp(
            prefix=".pairings-", suffix=".json", dir=str(self._pairing_dir)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            tmp_path.replace(self._path)
            self._path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
            raise

    def _read_keyring(self) -> str | None:
        try:
            return keyring.get_password(KEYRING_SERVICE, KEYRING_KEY)
        except KeyringError as exc:
            logger.warning("Keyring read failed (%s); falling back to file", exc)
            return None
