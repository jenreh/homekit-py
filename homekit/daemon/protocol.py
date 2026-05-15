"""Line-delimited JSON wire protocol between CLI and daemon.

Frame format: one JSON object per UTF-8 line, terminated by ``\\n``. Each frame is
at most ``MAX_FRAME_BYTES`` bytes including the terminator.

Message shapes::

    request   {"id": N, "method": str, "params": {...}}
    result    {"id": N, "result": <any>}
    error     {"id": N, "error": {"code": str, "message": str}}
    event     {"id": N, "event": <any>}    # streamed result frame
    end       {"id": N, "end": true}       # final stream frame
    cancel    {"id": N, "cancel": true}    # client -> server stream abort
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin

MAX_FRAME_BYTES: int = 1 << 20  # 1 MiB

ProtocolError = type("ProtocolError", (RuntimeError,), {})


def json_default(value: Any) -> Any:
    """JSON ``default=`` for dataclasses, paths, and (frozen)sets."""
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, frozenset):
        return sorted(value)
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value)!r} not serialisable")


def encode_frame(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, default=json_default).encode("utf-8") + b"\n"
    if len(raw) > MAX_FRAME_BYTES:
        raise ProtocolError(f"Frame exceeds {MAX_FRAME_BYTES} bytes")
    return raw


def decode_frame(line: bytes) -> dict[str, Any]:
    if len(line) > MAX_FRAME_BYTES:
        raise ProtocolError(f"Frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"Invalid JSON frame: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError(f"Frame must be JSON object, got {type(obj).__name__}")
    return obj


async def read_frame(reader: Any) -> dict[str, Any] | None:
    """Read one frame; return ``None`` on clean EOF."""
    line = await reader.readline()
    if not line:
        return None
    return decode_frame(line)


async def write_frame(writer: Any, payload: dict[str, Any]) -> None:
    writer.write(encode_frame(payload))
    await writer.drain()


# ----------------------------------------------------------------- (de)serialise


def dataclass_to_payload(value: Any) -> Any:
    """Eager-serialise a dataclass / collection tree to JSON-friendly values.

    Used when we want to return a payload directly (server side) instead of
    relying on ``json.dumps(default=...)``.
    """
    if is_dataclass(value):
        return {f.name: dataclass_to_payload(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [dataclass_to_payload(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, dict):
        return {str(k): dataclass_to_payload(v) for k, v in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def dataclass_from_payload(annotation: Any, payload: Any) -> Any:
    """Reconstruct a typed value from a JSON-decoded payload.

    Supports nested dataclasses, ``tuple[X, ...]``, ``list[X]``, ``dict[str, V]``,
    ``frozenset[X]``, ``Literal``, ``Optional`` (``X | None``), and primitives.
    """
    if payload is None:
        return None
    origin = get_origin(annotation)
    args = get_args(annotation)

    # ``X | None`` / ``Optional[X]`` / ``Union[X, Y]``
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in args if a is not type(None)]
        if not non_none:
            return None
        # First arg that successfully decodes wins; for our payloads the first
        # non-None arg is always the right one (Optional patterns).
        return dataclass_from_payload(non_none[0], payload)

    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(dataclass_from_payload(args[0], item) for item in payload)
        return tuple(
            dataclass_from_payload(arg, item) for arg, item in zip(args, payload, strict=False)
        )
    if origin is list:
        item_type = args[0] if args else Any
        return [dataclass_from_payload(item_type, item) for item in payload]
    if origin is frozenset:
        item_type = args[0] if args else Any
        return frozenset(dataclass_from_payload(item_type, item) for item in payload)
    if origin is set:
        item_type = args[0] if args else Any
        return {dataclass_from_payload(item_type, item) for item in payload}
    if origin is dict:
        key_type = args[0] if args else Any
        value_type = args[1] if len(args) > 1 else Any
        return {
            dataclass_from_payload(key_type, k): dataclass_from_payload(value_type, v)
            for k, v in payload.items()
        }
    if origin is typing.Literal:
        return payload

    if isinstance(annotation, type) and is_dataclass(annotation):
        hints = typing.get_type_hints(annotation)
        kwargs = {}
        for field in dataclasses.fields(annotation):
            if field.name in payload:
                kwargs[field.name] = dataclass_from_payload(
                    hints.get(field.name, Any), payload[field.name]
                )
        return annotation(**kwargs)

    if annotation is Path:
        return Path(payload)
    return payload


def dataclass_list_from_payload(item_type: type, payload: Any) -> list[Any]:
    return [dataclass_from_payload(item_type, item) for item in payload]
