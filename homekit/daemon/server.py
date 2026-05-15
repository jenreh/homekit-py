"""Async Unix-socket RPC server that exposes ``HomeKitClient`` to CLI processes.

A single ``HomeKitClient`` instance is shared across every connected CLI. The
server serialises BLE I/O per ``device_id`` so two clients targeting the same
accessory don't trample each other's GATT session.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from homekit.client import HomeKitClient
from homekit.core.models import HapEvent
from homekit.daemon.protocol import (
    ProtocolError,
    dataclass_to_payload,
    read_frame,
    write_frame,
)
from homekit.exceptions import HomeKitError

logger = logging.getLogger(__name__)

ERROR_CODE_MAP: dict[str, type[HomeKitError]] = {}


def _register_errors() -> None:
    # Subclass-first ordering so the deepest match wins lookup.
    from homekit import exceptions as exc_module

    for name in dir(exc_module):
        obj = getattr(exc_module, name)
        if isinstance(obj, type) and issubclass(obj, HomeKitError):
            ERROR_CODE_MAP[name] = obj


_register_errors()


def _error_code_for(exc: BaseException) -> str:
    # Most-specific subclass name from the exception's MRO.
    for cls in type(exc).__mro__:
        if cls is HomeKitError:
            break
        if cls.__name__ in ERROR_CODE_MAP:
            return cls.__name__
    return "HomeKitError"


# Method dispatch metadata: maps method name to a callable that runs against the
# shared HomeKitClient. Each entry returns a JSON-friendly payload (or yields
# events for streaming methods).
UnaryMethod = Callable[[HomeKitClient, dict[str, Any]], Awaitable[Any]]


def _make_unary_table() -> dict[str, UnaryMethod]:
    async def discover(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.discover(p.get("timeout_s"))

    async def pair(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.pair(p["device_id"], p["pin"], alias=p.get("alias"))

    async def unpair(c: HomeKitClient, p: dict[str, Any]) -> Any:
        await c.unpair(p["device_id"])
        return None

    async def list_pairings(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.list_pairings()

    async def get_accessories(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.get_accessories(p["device_id"], refresh=p.get("refresh", False))

    async def identify(c: HomeKitClient, p: dict[str, Any]) -> Any:
        await c.identify(p["device_id"])
        return None

    async def list_entities(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.list_entities(refresh=p.get("refresh", False))

    async def get_entity(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.get_entity(p["entity_id"])

    async def get_state(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.get_state(p["entity_id"], refresh=p.get("refresh", False))

    async def get_characteristic(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.get_characteristic(p["device_id"], p["aid"], p["iid"])

    async def put_characteristic(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.put_characteristic(p["device_id"], p["aid"], p["iid"], p["value"])

    async def turn_on(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.turn_on(p["entity_id"])

    async def turn_off(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.turn_off(p["entity_id"])

    async def set_brightness(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.set_brightness(p["entity_id"], float(p["value"]))

    async def set_color_temperature(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.set_color_temperature(p["entity_id"], int(p["kelvin"]))

    async def set_hue_saturation(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.set_hue_saturation(
            p["entity_id"], float(p["hue"]), float(p["saturation"])
        )

    async def set_target_temperature(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.set_target_temperature(p["entity_id"], float(p["celsius"]))

    async def set_target_mode(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.set_target_mode(p["entity_id"], int(p["mode_id"]))

    async def set_lock(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.set_lock(
            p["entity_id"],
            bool(p["locked"]),
            confirmation_token=p.get("confirmation_token"),
        )

    async def set_position(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.set_position(p["entity_id"], int(p["percent"]))

    async def set_rotation_speed(c: HomeKitClient, p: dict[str, Any]) -> Any:
        return await c.set_rotation_speed(p["entity_id"], int(p["percent"]))

    return {
        "discover": discover,
        "pair": pair,
        "unpair": unpair,
        "list_pairings": list_pairings,
        "get_accessories": get_accessories,
        "identify": identify,
        "list_entities": list_entities,
        "get_entity": get_entity,
        "get_state": get_state,
        "get_characteristic": get_characteristic,
        "put_characteristic": put_characteristic,
        "turn_on": turn_on,
        "turn_off": turn_off,
        "set_brightness": set_brightness,
        "set_color_temperature": set_color_temperature,
        "set_hue_saturation": set_hue_saturation,
        "set_target_temperature": set_target_temperature,
        "set_target_mode": set_target_mode,
        "set_lock": set_lock,
        "set_position": set_position,
        "set_rotation_speed": set_rotation_speed,
    }


_UNARY_METHODS = _make_unary_table()
_DEVICE_KEY_PARAMS = ("device_id",)
_ENTITY_KEY_PARAMS = ("entity_id",)


def _device_lock_key(method: str, params: dict[str, Any]) -> str | None:
    if method.startswith("set_") or method in {
        "turn_on",
        "turn_off",
        "get_state",
        "get_entity",
    }:
        # Entity-keyed methods need the device behind the entity; resolved
        # lazily inside the dispatch using the HomeKitClient entity index.
        return params.get("entity_id")
    for key in _DEVICE_KEY_PARAMS:
        if key in params:
            return str(params[key])
    for key in _ENTITY_KEY_PARAMS:
        if key in params:
            return str(params[key])
    return None


class DaemonServer:
    """Owns one ``HomeKitClient`` + one Unix-socket ``asyncio.Server``."""

    def __init__(
        self,
        client: HomeKitClient,
        socket_path: Path | str,
        *,
        idle_timeout_s: float = 600.0,
    ) -> None:
        self._client = client
        self._socket_path = Path(socket_path)
        self._idle_timeout_s = idle_timeout_s
        self._server: asyncio.AbstractServer | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._active_connections = 0
        self._last_activity = 0.0
        self._idle_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        if self._server is not None:
            return
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        await self._client.start()
        self._server = await asyncio.start_unix_server(
            self._handle_connection, path=str(self._socket_path)
        )
        self._socket_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        self._last_activity = asyncio.get_event_loop().time()
        self._idle_task = asyncio.create_task(self._idle_monitor())
        logger.info("Daemon listening on %s", self._socket_path)

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        try:
            await self._shutdown_event.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._idle_task is not None:
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
            self._idle_task = None
        # Force-close any live client connections so wait_closed() doesn't
        # deadlock on a peer that's blocked reading.
        for writer in list(self._writers):
            with contextlib.suppress(Exception):
                writer.close()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(BaseException):
                await task
        self._tasks.clear()
        self._writers.clear()
        with contextlib.suppress(FileNotFoundError):
            self._socket_path.unlink()
        await self._client.stop()
        logger.info("Daemon stopped")

    async def _idle_monitor(self) -> None:
        try:
            while not self._shutdown_event.is_set():
                await asyncio.sleep(min(self._idle_timeout_s, 30.0))
                if self._active_connections > 0:
                    continue
                idle = asyncio.get_event_loop().time() - self._last_activity
                if idle >= self._idle_timeout_s:
                    logger.info("Idle %.0fs >= %.0fs, shutting down", idle, self._idle_timeout_s)
                    self._shutdown_event.set()
                    return
        except asyncio.CancelledError:
            return

    # ----------------------------------------------------------- connection loop

    async def _handle_connection(  # noqa: PLR0915
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._active_connections += 1
        self._last_activity = asyncio.get_event_loop().time()
        self._writers.add(writer)
        active_streams: dict[int, asyncio.Task[None]] = {}
        peer = writer.get_extra_info("peername") or "<unix>"
        logger.debug("Client connected: %s", peer)
        try:
            while True:
                try:
                    frame = await read_frame(reader)
                except ProtocolError as exc:
                    await self._send_error(writer, None, "ProtocolError", str(exc))
                    break
                if frame is None:
                    break
                request_id = frame.get("id")
                if frame.get("cancel"):
                    task = active_streams.pop(int(request_id), None)
                    if task is not None:
                        task.cancel()
                    continue
                method = frame.get("method")
                if not isinstance(request_id, int) or not isinstance(method, str):
                    await self._send_error(
                        writer, request_id, "ProtocolError", "missing id/method"
                    )
                    continue
                params = frame.get("params") or {}
                if method == "ping":
                    await write_frame(writer, {"id": request_id, "result": "pong"})
                    continue
                if method == "shutdown":
                    await write_frame(writer, {"id": request_id, "result": "ok"})
                    self._shutdown_event.set()
                    continue
                if method == "listen":
                    task = asyncio.create_task(
                        self._stream_listen(writer, request_id, params)
                    )
                    active_streams[request_id] = task
                    task.add_done_callback(lambda _t, _i=request_id: active_streams.pop(_i, None))
                    continue
                handler = _UNARY_METHODS.get(method)
                if handler is None:
                    await self._send_error(
                        writer, request_id, "UnknownMethod", f"Unknown method {method!r}"
                    )
                    continue
                await self._dispatch_unary(writer, request_id, method, params, handler)
                self._last_activity = asyncio.get_event_loop().time()
        finally:
            for task in active_streams.values():
                task.cancel()
            for task in active_streams.values():
                with contextlib.suppress(BaseException):
                    await task
            self._active_connections -= 1
            self._last_activity = asyncio.get_event_loop().time()
            self._writers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
            logger.debug("Client disconnected: %s", peer)

    async def _dispatch_unary(
        self,
        writer: asyncio.StreamWriter,
        request_id: int,
        method: str,
        params: dict[str, Any],
        handler: UnaryMethod,
    ) -> None:
        lock_key = _device_lock_key(method, params)
        lock = self._locks.setdefault(lock_key, asyncio.Lock()) if lock_key else None
        try:
            if lock is not None:
                async with lock:
                    result = await handler(self._client, params)
            else:
                result = await handler(self._client, params)
        except HomeKitError as exc:
            await self._send_error(writer, request_id, _error_code_for(exc), str(exc))
            return
        except KeyError as exc:
            await self._send_error(
                writer, request_id, "ProtocolError", f"missing param: {exc}"
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Method %s failed", method)
            await self._send_error(writer, request_id, "HomeKitError", str(exc))
            return
        payload = dataclass_to_payload(result)
        await write_frame(writer, {"id": request_id, "result": payload})

    async def _stream_listen(
        self,
        writer: asyncio.StreamWriter,
        request_id: int,
        params: dict[str, Any],
    ) -> None:
        entity_ids = params.get("entity_ids")
        try:
            stream = self._client.listen(entity_ids)
            async for event in stream:
                payload = dataclass_to_payload(event)
                await write_frame(writer, {"id": request_id, "event": payload})
        except asyncio.CancelledError:
            raise
        except HomeKitError as exc:
            await self._send_error(writer, request_id, _error_code_for(exc), str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("listen stream failed")
            await self._send_error(writer, request_id, "HomeKitError", str(exc))
            return
        await write_frame(writer, {"id": request_id, "end": True})

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        request_id: int | None,
        code: str,
        message: str,
    ) -> None:
        with contextlib.suppress(Exception):
            await write_frame(
                writer,
                {"id": request_id, "error": {"code": code, "message": message}},
            )


__all__ = ["ERROR_CODE_MAP", "DaemonServer", "HapEvent"]
