from __future__ import annotations

from unittest.mock import MagicMock

from homekit.discovery import HAP_SERVICE, parse_service_info


def _info(name: str, props: dict[str, str], addresses: list[bytes], port: int = 12345):
    info = MagicMock()
    info.name = f"{name}.{HAP_SERVICE}"
    info.properties = {k.encode(): v.encode() for k, v in props.items()}
    info.addresses = addresses
    info.parsed_addresses = MagicMock(return_value=["10.0.0.10"])
    info.port = port
    return info


def test_parse_returns_none_without_addresses() -> None:
    info = _info("Foo", {"id": "AA:BB"}, addresses=[])
    assert parse_service_info(info) is None


def test_parse_returns_none_without_device_id() -> None:
    info = _info("Foo", {"md": "Test"}, addresses=[b"\n\x00\x00\x01"])
    assert parse_service_info(info) is None


def test_parse_extracts_txt_fields() -> None:
    info = _info(
        "EveEnergy._hap",
        {
            "id": "AA:BB:CC:DD:EE:FF",
            "md": "Eve Energy",
            "ci": "7",
            "sf": "0",
            "c#": "3",
        },
        addresses=[b"\n\x00\x00\x01"],
    )
    accessory = parse_service_info(info)
    assert accessory is not None
    assert accessory.device_id == "AA:BB:CC:DD:EE:FF"
    assert accessory.category == 7
    assert accessory.category_name == "Outlet"
    assert accessory.is_paired
    assert accessory.config_number == 3
    assert not accessory.is_bridge


def test_parse_detects_bridge_category() -> None:
    info = _info(
        "Bridge._hap",
        {"id": "BR:01", "ci": "2", "sf": "1"},
        addresses=[b"\n\x00\x00\x01"],
    )
    accessory = parse_service_info(info)
    assert accessory is not None
    assert accessory.is_bridge
    assert not accessory.is_paired


def test_parse_handles_invalid_numbers() -> None:
    info = _info(
        "Bad._hap",
        {"id": "BAD:01", "ci": "x", "sf": "x", "c#": "x"},
        addresses=[b"\n\x00\x00\x01"],
    )
    accessory = parse_service_info(info)
    assert accessory is not None
    assert accessory.category == 1
    assert accessory.config_number == 0
