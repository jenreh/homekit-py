from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from homekit.core.storage import PairingStore
from homekit.exceptions import PairingStoreCorruptError


def test_ensure_file_creates_empty(tmp_path: Path) -> None:
    store = PairingStore(tmp_path, backend="file")
    store.ensure_file()
    assert store.path.exists()
    assert store.path.read_text(encoding="utf-8") == "{}"


def test_ensure_file_chmod_0600(tmp_path: Path) -> None:
    store = PairingStore(tmp_path, backend="file")
    store.ensure_file()
    mode = stat.S_IMODE(store.path.stat().st_mode)
    assert mode == 0o600


def test_export_returns_dict(tmp_path: Path) -> None:
    store = PairingStore(tmp_path, backend="file")
    store.import_dict({"alias": {"AccessoryPairingID": "AA:BB"}})
    assert store.export_dict()["alias"]["AccessoryPairingID"] == "AA:BB"


def test_get_alias_for_device(tmp_path: Path) -> None:
    store = PairingStore(tmp_path, backend="file")
    store.import_dict({"living": {"AccessoryPairingID": "AA:BB:CC"}})
    assert store.get_alias_for_device("AA:BB:CC") == "living"
    assert store.get_alias_for_device("11:22:33") is None


def test_list_aliases_returns_sorted(tmp_path: Path) -> None:
    store = PairingStore(tmp_path, backend="file")
    store.import_dict({"b": {}, "a": {}})
    assert store.list_aliases() == ["a", "b"]


def test_corrupt_file_raises(tmp_path: Path) -> None:
    store = PairingStore(tmp_path, backend="file")
    path = store.path
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(PairingStoreCorruptError):
        store.export_dict()


def test_keyring_hydration_writes_file_when_missing(tmp_path: Path) -> None:
    store = PairingStore(tmp_path, backend="keyring")
    with patch(
        "homekit.core.storage.keyring.get_password",
        return_value=json.dumps({"k": {"AccessoryPairingID": "AA"}}),
    ):
        store.ensure_file()
    assert store.path.exists()
    assert json.loads(store.path.read_text(encoding="utf-8"))["k"]["AccessoryPairingID"] == "AA"
