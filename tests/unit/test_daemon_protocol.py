"""Wire-protocol unit tests: frame encoding, decoding, dataclass reconstruction."""

from __future__ import annotations

import asyncio

import pytest

from homekit.core.models import (
    Accessory,
    Characteristic,
    Entity,
    EntityCapability,
    EntityState,
    Service,
)
from homekit.daemon.protocol import (
    MAX_FRAME_BYTES,
    ProtocolError,
    dataclass_from_payload,
    dataclass_to_payload,
    decode_frame,
    encode_frame,
    read_frame,
)


def test_encode_decode_roundtrip() -> None:
    payload = {"id": 1, "method": "ping", "params": {}}
    encoded = encode_frame(payload)
    assert encoded.endswith(b"\n")
    assert decode_frame(encoded.rstrip(b"\n")) == payload


def test_decode_rejects_non_object() -> None:
    with pytest.raises(ProtocolError):
        decode_frame(b"[1, 2]")


def test_decode_rejects_bad_json() -> None:
    with pytest.raises(ProtocolError):
        decode_frame(b"{not json")


def test_encode_oversize_raises() -> None:
    huge = "x" * (MAX_FRAME_BYTES + 100)
    with pytest.raises(ProtocolError):
        encode_frame({"value": huge})


async def test_read_frame_returns_none_on_eof() -> None:
    reader = asyncio.StreamReader()
    reader.feed_eof()
    assert await read_frame(reader) is None


async def test_read_frame_parses_line() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"id":7,"result":42}\n')
    frame = await read_frame(reader)
    assert frame == {"id": 7, "result": 42}


def test_dataclass_to_and_from_payload_characteristic() -> None:
    char = Characteristic(
        aid=1,
        iid=10,
        type_uuid="00000025",
        type_name="On",
        value=False,
        format="bool",
        perms=("pr", "pw", "ev"),
    )
    payload = dataclass_to_payload(char)
    rebuilt = dataclass_from_payload(Characteristic, payload)
    assert rebuilt == char


def test_dataclass_from_payload_handles_nested_tuples() -> None:
    char = Characteristic(1, 2, "00000023", "Name", "x", "string", ("pr",))
    svc = Service(1, 1, "0000003E", "AccessoryInformation", (char,), is_primary=False)
    accessory = Accessory(1, "AA:BB:CC", "Test", (svc,))
    payload = dataclass_to_payload(accessory)
    rebuilt = dataclass_from_payload(Accessory, payload)
    assert rebuilt == accessory


def test_dataclass_from_payload_frozenset_field() -> None:
    cap = EntityCapability(
        domain="light",
        readable=frozenset({"On", "Brightness"}),
        writable=frozenset({"On"}),
    )
    entity = Entity(
        entity_id="light.test",
        domain="light",
        name="Test",
        device_id="AA:BB:CC",
        aid=1,
        service_iid=9,
        capability=cap,
    )
    payload = dataclass_to_payload(entity)
    rebuilt = dataclass_from_payload(Entity, payload)
    assert rebuilt.capability.readable == frozenset({"On", "Brightness"})
    assert rebuilt == entity


def test_dataclass_from_payload_optional_union() -> None:
    state = EntityState(
        entity_id="light.test",
        state="on",
        attributes={"brightness": 50, "on": True, "missing": None},
        last_seen="2026-01-01T00:00:00+00:00",
        source="poll",
        fresh=True,
    )
    payload = dataclass_to_payload(state)
    rebuilt = dataclass_from_payload(EntityState, payload)
    assert rebuilt == state
