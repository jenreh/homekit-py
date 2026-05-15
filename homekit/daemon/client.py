"""Client side of the daemon RPC: speaks the wire protocol and rebuilds dataclasses.

``RemoteHomeKitClient`` mirrors ``HomeKitClient``'s public surface so it can be
used as a drop-in replacement by the CLI when the daemon is enabled.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from types import TracebackType
from typing import Any

from homekit.core.models import (
    Accessory,
    AccessoryPairing,
    Characteristic,
    CharacteristicWriteResult,
    DiscoveredAccessory,
    Entity,
    EntityState,
    HapEvent,
)
from homekit.daemon.protocol import (
    ProtocolError,
    dataclass_from_payload,
    read_frame,
    write_frame,
)
from homekit.exceptions import (
    AccessoryNotFoundError,
    AlreadyPairedError,
    CharacteristicNotWritableError,
    ConnectionLimitError,
    HomeKitError,
    NotPairableError,
    NotPairedError,
    PairingError,
    PairingStoreCorruptError,
    PolicyBlockedError,
)

logger = logging.getLogger(__name__)


_ERROR_CLASSES: dict[str, type[HomeKitError]] = {
    "AccessoryNotFoundError": AccessoryNotFoundError,
    "AlreadyPairedError": AlreadyPairedError,
    "CharacteristicNotWritableError": CharacteristicNotWritableError,
    "ConnectionLimitError": ConnectionLimitError,
    "HomeKitError": HomeKitError,
    "NotPairableError": NotPairableError,
    "NotPairedError": NotPairedError,
    "PairingError": PairingError,
    "PairingStoreCorruptError": PairingStoreCorruptError,
    "PolicyBlockedError": PolicyBlockedError,
}


def _raise_remote(code: str, message: str) -> HomeKitError:
    cls = _ERROR_CLASSES.get(code, HomeKitError)
    return cls(message)


class DaemonRpcClient:
    """Owns one Unix-socket connection + a request/response demultiplexer."""

    def __init__(self, socket_path: Path | str) -> None:
        self._socket_path = Path(socket_path)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._streams: dict[int, asyncio.Queue[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> DaemonRpcClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._reader is not None:
            return
        self._reader, self._writer = await asyncio.open_unix_connection(
            path=str(self._socket_path)
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass
            self._reader_task = None
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001, S110
                pass
            self._writer = None
        self._reader = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(HomeKitError("Daemon connection closed"))
        self._pending.clear()
        for q in self._streams.values():
            await q.put({"__closed__": True})
        self._streams.clear()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                try:
                    frame = await read_frame(self._reader)
                except ProtocolError as exc:
                    logger.warning("Bad frame from daemon: %s", exc)
                    continue
                if frame is None:
                    break
                self._dispatch(frame)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Daemon reader stopped: %s", exc)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(HomeKitError("Daemon connection closed"))
            self._pending.clear()
            for q in self._streams.values():
                await q.put({"__closed__": True})

    def _dispatch(self, frame: dict[str, Any]) -> None:
        request_id = frame.get("id")
        if not isinstance(request_id, int):
            return
        if "event" in frame:
            q = self._streams.get(request_id)
            if q is not None:
                q.put_nowait(frame)
            return
        if frame.get("end"):
            q = self._streams.get(request_id)
            if q is not None:
                q.put_nowait({"__end__": True})
            return
        fut = self._pending.pop(request_id, None)
        if fut is None or fut.done():
            return
        if "error" in frame:
            err = frame["error"]
            fut.set_exception(_raise_remote(err.get("code", "HomeKitError"), err.get("message", "")))
        else:
            fut.set_result(frame.get("result"))

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._writer is None:
            raise HomeKitError("Daemon client not connected")
        request_id = next(self._ids)
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[request_id] = fut
        async with self._lock:
            await write_frame(
                self._writer,
                {"id": request_id, "method": method, "params": params or {}},
            )
        return await fut

    async def stream(
        self, method: str, params: dict[str, Any] | None = None
    ) -> AsyncIterator[Any]:
        if self._writer is None:
            raise HomeKitError("Daemon client not connected")
        request_id = next(self._ids)
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._streams[request_id] = queue
        async with self._lock:
            await write_frame(
                self._writer,
                {"id": request_id, "method": method, "params": params or {}},
            )
        try:
            while True:
                frame = await queue.get()
                if frame.get("__end__"):
                    return
                if frame.get("__closed__"):
                    raise HomeKitError("Daemon connection closed during stream")
                if "error" in frame:
                    err = frame["error"]
                    raise _raise_remote(err.get("code", "HomeKitError"), err.get("message", ""))
                yield frame.get("event")
        finally:
            self._streams.pop(request_id, None)
            if self._writer is not None:
                try:
                    async with self._lock:
                        await write_frame(
                            self._writer, {"id": request_id, "cancel": True}
                        )
                except Exception:  # noqa: BLE001, S110
                    pass


class RemoteHomeKitClient:
    """Mirror of ``HomeKitClient`` that proxies through a ``DaemonRpcClient``.

    Wraps the raw RPC layer, reconstructing dataclasses on receipt so callers see
    the exact same types they would from the in-process client.
    """

    def __init__(self, rpc: DaemonRpcClient) -> None:
        self._rpc = rpc

    async def __aenter__(self) -> RemoteHomeKitClient:
        await self._rpc.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._rpc.close()

    async def start(self) -> None:
        await self._rpc.connect()

    async def stop(self) -> None:
        await self._rpc.close()

    async def discover(
        self, timeout_s: float | None = None
    ) -> list[DiscoveredAccessory]:
        payload = await self._rpc.call("discover", {"timeout_s": timeout_s})
        return [dataclass_from_payload(DiscoveredAccessory, item) for item in payload]

    async def pair(
        self, device_id: str, pin: str, alias: str | None = None
    ) -> AccessoryPairing:
        payload = await self._rpc.call(
            "pair", {"device_id": device_id, "pin": pin, "alias": alias}
        )
        return dataclass_from_payload(AccessoryPairing, payload)

    async def unpair(self, device_id: str) -> None:
        await self._rpc.call("unpair", {"device_id": device_id})

    async def list_pairings(self) -> list[AccessoryPairing]:
        payload = await self._rpc.call("list_pairings")
        return [dataclass_from_payload(AccessoryPairing, item) for item in payload]

    async def get_accessories(
        self, device_id: str, *, refresh: bool = False
    ) -> list[Accessory]:
        payload = await self._rpc.call(
            "get_accessories", {"device_id": device_id, "refresh": refresh}
        )
        return [dataclass_from_payload(Accessory, item) for item in payload]

    async def identify(self, device_id: str) -> None:
        await self._rpc.call("identify", {"device_id": device_id})

    async def list_entities(self, *, refresh: bool = False) -> list[Entity]:
        payload = await self._rpc.call("list_entities", {"refresh": refresh})
        return [dataclass_from_payload(Entity, item) for item in payload]

    async def get_entity(self, entity_id: str) -> Entity:
        payload = await self._rpc.call("get_entity", {"entity_id": entity_id})
        return dataclass_from_payload(Entity, payload)

    async def get_state(self, entity_id: str, *, refresh: bool = False) -> EntityState:
        payload = await self._rpc.call(
            "get_state", {"entity_id": entity_id, "refresh": refresh}
        )
        return dataclass_from_payload(EntityState, payload)

    async def get_characteristic(
        self, device_id: str, aid: int, iid: int
    ) -> Characteristic:
        payload = await self._rpc.call(
            "get_characteristic",
            {"device_id": device_id, "aid": aid, "iid": iid},
        )
        return dataclass_from_payload(Characteristic, payload)

    async def put_characteristic(
        self, device_id: str, aid: int, iid: int, value: Any
    ) -> CharacteristicWriteResult:
        payload = await self._rpc.call(
            "put_characteristic",
            {"device_id": device_id, "aid": aid, "iid": iid, "value": value},
        )
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def turn_on(self, entity_id: str) -> CharacteristicWriteResult:
        payload = await self._rpc.call("turn_on", {"entity_id": entity_id})
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def turn_off(self, entity_id: str) -> CharacteristicWriteResult:
        payload = await self._rpc.call("turn_off", {"entity_id": entity_id})
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def set_brightness(
        self, entity_id: str, value: float
    ) -> CharacteristicWriteResult:
        payload = await self._rpc.call(
            "set_brightness", {"entity_id": entity_id, "value": value}
        )
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def set_color_temperature(
        self, entity_id: str, kelvin: int
    ) -> CharacteristicWriteResult:
        payload = await self._rpc.call(
            "set_color_temperature", {"entity_id": entity_id, "kelvin": kelvin}
        )
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def set_hue_saturation(
        self, entity_id: str, hue: float, saturation: float
    ) -> CharacteristicWriteResult:
        payload = await self._rpc.call(
            "set_hue_saturation",
            {"entity_id": entity_id, "hue": hue, "saturation": saturation},
        )
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def set_target_temperature(
        self, entity_id: str, celsius: float
    ) -> CharacteristicWriteResult:
        payload = await self._rpc.call(
            "set_target_temperature",
            {"entity_id": entity_id, "celsius": celsius},
        )
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def set_target_mode(
        self, entity_id: str, mode_id: int
    ) -> CharacteristicWriteResult:
        payload = await self._rpc.call(
            "set_target_mode", {"entity_id": entity_id, "mode_id": mode_id}
        )
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def set_lock(
        self,
        entity_id: str,
        locked: bool,
        *,
        confirmation_token: str | None = None,
    ) -> CharacteristicWriteResult:
        payload = await self._rpc.call(
            "set_lock",
            {
                "entity_id": entity_id,
                "locked": locked,
                "confirmation_token": confirmation_token,
            },
        )
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def set_position(
        self, entity_id: str, percent: int
    ) -> CharacteristicWriteResult:
        payload = await self._rpc.call(
            "set_position", {"entity_id": entity_id, "percent": percent}
        )
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def set_rotation_speed(
        self, entity_id: str, percent: int
    ) -> CharacteristicWriteResult:
        payload = await self._rpc.call(
            "set_rotation_speed", {"entity_id": entity_id, "percent": percent}
        )
        return dataclass_from_payload(CharacteristicWriteResult, payload)

    async def listen(
        self, entity_ids: Iterable[str] | None = None
    ) -> AsyncIterator[HapEvent]:
        params = {"entity_ids": list(entity_ids) if entity_ids else None}
        async for event_payload in self._rpc.stream("listen", params):
            yield dataclass_from_payload(HapEvent, event_payload)
