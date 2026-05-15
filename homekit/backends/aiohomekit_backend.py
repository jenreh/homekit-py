"""The single point of contact between homekit-py and `aiohomekit`.

If aiohomekit ever introduces a breaking change, only this file needs to move.
All other modules consume the ``HomeKitBackend`` Protocol.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from aiohomekit import Controller
from aiohomekit.exceptions import (
    AccessoryDisconnectedError,
    AuthenticationError,
    UnknownError,
)
from aiohomekit.model import Accessories, AccessoriesState
from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.model.services import ServicesTypes
from bleak import BleakScanner
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

from homekit.core.cache import AccessoryCache
from homekit.core.models import (
    Accessory,
    AccessoryPairing,
    Characteristic,
    CharacteristicWriteResult,
    DiscoveredAccessory,
    HapEvent,
    Service,
    category_name,
)
from homekit.core.storage import PairingStore
from homekit.exceptions import (
    AccessoryNotFoundError,
    AlreadyPairedError,
    CharacteristicNotWritableError,
    NotPairableError,
    NotPairedError,
    PairingError,
)

if TYPE_CHECKING:
    from aiohomekit.controller.abstract import AbstractDiscovery, AbstractPairing

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bleak compat shim
# ---------------------------------------------------------------------------
# aiohomekit 3.2.x was written against bleak <0.21 which had
# ``BleakScanner.register_detection_callback()``.  That method was removed in
# bleak 0.21.  We subclass BleakScanner, wiring the new constructor-based
# detection_callback API back into the old method so aiohomekit's BleController
# continues to work without modification.


class _CompatBleakScanner(BleakScanner):
    """BleakScanner with ``register_detection_callback`` shim for aiohomekit."""

    def __init__(self) -> None:
        self._pending_callback: Any = None
        super().__init__(detection_callback=self._on_detect)

    def _on_detect(self, device: Any, advertisement_data: Any) -> None:
        if self._pending_callback is None:
            return
        try:
            self._pending_callback(device, advertisement_data)
        except AttributeError as exc:
            # aiohomekit 3.2.x crashes when an advert arrives for a paired
            # accessory whose accessories_state hasn't been populated yet
            # (pairing.py:_update_cached_state_num accesses .state_num on
            # None). Swallow so the scanner keeps running.
            logger.debug("BLE detect callback raised: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("BLE detect callback raised: %s", exc)

    def register_detection_callback(self, callback: Any) -> None:
        """Re-implement the API removed in bleak 0.21 so aiohomekit can call it."""
        self._pending_callback = callback


def _build_reverse_map(cls: type) -> dict[str, str]:
    """Map UUID → constant name from the bare ``ServicesTypes`` / ``CharacteristicsTypes`` classes."""
    mapping: dict[str, str] = {}
    for attr in dir(cls):
        if attr.startswith("_"):
            continue
        value = getattr(cls, attr)
        if isinstance(value, str) and len(value) == 36 and value.count("-") == 4:
            short = value.split("-")[0].upper().lstrip("0") or "0"
            display = _humanize(attr)
            mapping[value.upper()] = display
            mapping[short] = display
    return mapping


def _humanize(constant: str) -> str:
    return "".join(part.capitalize() for part in constant.split("_"))


_SERVICE_NAMES = _build_reverse_map(ServicesTypes)
_CHAR_NAMES = _build_reverse_map(CharacteristicsTypes)


def _noop_handler(*_: Any, **__: Any) -> None:
    """Required by zeroconf's `AsyncServiceBrowser`; aiohomekit drives state itself."""


def _ensure_accessories_state(pairing: Any) -> None:
    """Initialise ``pairing._accessories_state`` to an empty placeholder.

    aiohomekit 3.2.x reads ``_accessories_state.state_num`` from the BLE
    advertisement handler before the first GET /accessories call. For freshly
    paired BLE devices the cache is empty, so the attribute is ``None`` and
    the handler crashes. Pre-populating with an empty ``AccessoriesState``
    keeps the handler happy until real accessory data is fetched.
    """
    if getattr(pairing, "_accessories_state", None) is None:
        try:
            pairing._accessories_state = AccessoriesState(  # noqa: SLF001
                accessories=Accessories(),
                config_num=0,
                broadcast_key=None,
                state_num=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to seed accessories_state: %s", exc)


def _normalize_uuid(value: str) -> str:
    if not value:
        return value
    upper = value.upper()
    if "-" in upper:
        return upper.split("-")[0].lstrip("0") or "0"
    return upper.lstrip("0") or "0"


def _service_name(uuid: str) -> str | None:
    if not uuid:
        return None
    return _SERVICE_NAMES.get(uuid.upper()) or _SERVICE_NAMES.get(_normalize_uuid(uuid))


def _characteristic_name(uuid: str) -> str | None:
    if not uuid:
        return None
    return _CHAR_NAMES.get(uuid.upper()) or _CHAR_NAMES.get(_normalize_uuid(uuid))


def _convert_characteristic(raw: dict[str, Any], aid: int) -> Characteristic:
    type_uuid = str(raw.get("type", ""))
    perms = tuple(str(p) for p in raw.get("perms", ()))
    return Characteristic(
        aid=aid,
        iid=int(raw.get("iid", 0)),
        type_uuid=type_uuid,
        type_name=_characteristic_name(type_uuid),
        value=raw.get("value"),
        format=str(raw.get("format", "")),
        perms=perms,
        unit=raw.get("unit"),
        min_value=_as_float(raw.get("minValue")),
        max_value=_as_float(raw.get("maxValue")),
        min_step=_as_float(raw.get("minStep")),
    )


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _convert_service(raw: dict[str, Any], aid: int) -> Service:
    type_uuid = str(raw.get("type", ""))
    chars = tuple(
        _convert_characteristic(c, aid) for c in raw.get("characteristics", ())
    )
    return Service(
        aid=aid,
        iid=int(raw.get("iid", 0)),
        type_uuid=type_uuid,
        type_name=_service_name(type_uuid),
        characteristics=chars,
        is_primary=bool(raw.get("primary", False)),
    )


def _convert_accessory(raw: dict[str, Any], device_id: str) -> Accessory:
    aid = int(raw.get("aid", 1))
    services = tuple(_convert_service(s, aid) for s in raw.get("services", ()))
    name = ""
    for svc in services:
        if svc.type_name == "AccessoryInformation":
            for char in svc.characteristics:
                if char.type_name == "Name" and isinstance(char.value, str):
                    name = char.value
                    break
            if name:
                break
    return Accessory(
        aid=aid, device_id=device_id, name=name or device_id, services=services
    )


class AiohomekitBackend:
    """Implements ``HomeKitBackend`` by delegating to aiohomekit."""

    def __init__(
        self,
        store: PairingStore,
        cache: AccessoryCache,
        *,
        ble_enabled: bool = True,
        thread_enabled: bool = True,
    ) -> None:
        self._store = store
        self._cache = cache
        self._ble_enabled = ble_enabled
        self._thread_enabled = thread_enabled
        self._controller: Controller | None = None
        self._aiozc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._scanner: BleakScanner | None = None
        self._pairings: dict[str, AbstractPairing] = {}

    async def start(self) -> None:
        if self._controller is not None:
            return
        self._aiozc = AsyncZeroconf()
        types = ["_hap._tcp.local."]
        if self._thread_enabled:
            types.append("_hap._udp.local.")
        self._browser = AsyncServiceBrowser(
            self._aiozc.zeroconf,
            types,
            handlers=[_noop_handler],
        )
        scanner: BleakScanner | None = None
        if self._ble_enabled:
            self._scanner = _CompatBleakScanner()
            scanner = self._scanner
            logger.info("BLE scanning enabled")
        self._controller = Controller(
            async_zeroconf_instance=self._aiozc,
            bleak_scanner_instance=scanner,
        )
        await self._controller.async_start()
        self._store.ensure_file()
        try:
            self._controller.load_data(str(self._store.path))
        except FileNotFoundError:
            logger.debug("No pairing file present yet — first run")
        for key in list(self._controller.pairings):  # type: ignore[attr-defined]
            try:
                pairing = self._controller.pairings[key]  # type: ignore[index]
            except KeyError:
                continue
            _ensure_accessories_state(pairing)
            device_id = self._device_id_for_pairing(pairing)
            if device_id:
                self._pairings[device_id] = pairing

    async def stop(self) -> None:
        for pairing in self._pairings.values():
            try:
                await pairing.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("close failed for pairing: %s", exc)
        self._pairings.clear()
        if self._controller is not None:
            try:
                await self._controller.async_stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Controller stop raised: %s", exc)
            self._controller = None
        if self._browser is not None:
            try:
                await self._browser.async_cancel()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Browser cancel raised: %s", exc)
            self._browser = None
        if self._aiozc is not None:
            try:
                await self._aiozc.async_close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("AsyncZeroconf close raised: %s", exc)
            self._aiozc = None
        self._scanner = None

    # ------------------------------------------------------------------ discovery

    async def discover(self, timeout_s: float = 5.0) -> list[DiscoveredAccessory]:
        """Poll all transports for up to ``timeout_s`` seconds.

        BLE accessories advertise intermittently (battery-powered Eve devices
        can be silent for 30+ s between adverts). One pass at the end of the
        window misses them; instead poll every 0.5 s, accumulating uniques.
        """
        ctrl = self._require_controller()
        found: dict[str, DiscoveredAccessory] = {}
        deadline = asyncio.get_event_loop().time() + max(timeout_s, 1.0)
        while True:
            async for discovery in ctrl.async_discover():
                accessory = self._discovery_to_dataclass(discovery)
                if accessory is not None and accessory.device_id not in found:
                    found[accessory.device_id] = accessory
                    logger.debug(
                        "discovered %s (%s, transport=%s)",
                        accessory.name,
                        accessory.device_id,
                        getattr(accessory, "transport", "?"),
                    )
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.5)
        return list(found.values())

    # ------------------------------------------------------------------ pairing

    async def pair(self, device_id: str, pin: str, alias: str) -> AccessoryPairing:
        ctrl = self._require_controller()
        discovery = await self._find_discovery(ctrl, device_id)
        if discovery.paired:
            raise AlreadyPairedError(
                f"Accessory {device_id} reports it is already paired (sf=0)"
            )
        success = False
        try:
            finish = await discovery.async_start_pairing(alias)
            pairing = await finish(pin)
            success = True
        except AuthenticationError as exc:
            raise PairingError(f"Pairing rejected: {exc}") from exc
        except UnknownError as exc:
            raise NotPairableError(f"Accessory refused pairing: {exc}") from exc
        finally:
            if not success:
                close = getattr(discovery, "_close", None) or getattr(
                    discovery, "close", None
                )
                if callable(close):
                    try:
                        result = close()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Discovery cleanup raised: %s", exc)
        device = device_id.upper()
        # aiohomekit's BLE/IP discovery `finish_pairing` writes the new pairing
        # into the sub-controller but does not always register it on the parent
        # controller. Reload through `Controller.load_pairing` so `save_data`
        # picks it up via `self.aliases`.
        try:
            ctrl.load_pairing(alias, dict(pairing.pairing_data))
        except Exception as exc:  # noqa: BLE001
            logger.debug("load_pairing post-finish raised: %s", exc)
        _ensure_accessories_state(pairing)
        self._pairings[device] = pairing
        ctrl.save_data(str(self._store.path))
        self._store.sync_to_secure_store()
        return AccessoryPairing(
            device_id=device,
            host=str(getattr(discovery.description, "address", "") or ""),
            port=int(getattr(discovery.description, "port", 0) or 0),
            name=alias,
            paired_at=dt.datetime.now(dt.UTC).isoformat(),
        )

    async def unpair(self, device_id: str) -> None:
        ctrl = self._require_controller()
        alias = self._store.get_alias_for_device(device_id)
        if alias is None:
            raise NotPairedError(f"No pairing stored for {device_id}")
        try:
            await ctrl.remove_pairing(alias)
        except Exception as exc:  # noqa: BLE001
            logger.warning("remove_pairing failed: %s", exc)
        self._pairings.pop(device_id.upper(), None)
        ctrl.save_data(str(self._store.path))
        self._store.sync_to_secure_store()

    async def list_pairings(self) -> list[AccessoryPairing]:
        ctrl = self._require_controller()
        out: list[AccessoryPairing] = []
        # `ctrl.aliases` is keyed by the human-readable alias, `ctrl.pairings`
        # is keyed by the device-id. Merge both so we get a stable alias even
        # when the underlying pairing only registered itself in one of them.
        seen: set[str] = set()
        for alias, pairing in ctrl.aliases.items():  # type: ignore[attr-defined]
            device_id = self._device_id_for_pairing(pairing) or alias
            description = getattr(pairing, "description", None)
            out.append(
                AccessoryPairing(
                    device_id=device_id.upper(),
                    host=str(getattr(description, "address", "") or ""),
                    port=int(getattr(description, "port", 0) or 0),
                    name=alias,
                    paired_at="",
                )
            )
            seen.add(device_id.upper())
        for device_id_key, pairing in ctrl.pairings.items():  # type: ignore[attr-defined]
            device_id = self._device_id_for_pairing(pairing) or device_id_key
            if device_id.upper() in seen:
                continue
            description = getattr(pairing, "description", None)
            alias = self._store.get_alias_for_device(device_id) or device_id_key
            out.append(
                AccessoryPairing(
                    device_id=device_id.upper(),
                    host=str(getattr(description, "address", "") or ""),
                    port=int(getattr(description, "port", 0) or 0),
                    name=alias,
                    paired_at="",
                )
            )
        return out

    # ------------------------------------------------------------------ accessories

    async def list_accessories(
        self, device_id: str, *, refresh: bool = False
    ) -> list[Accessory]:
        pairing = await self._require_pairing(device_id)
        config_number = int(getattr(pairing, "config_num", 0) or 0)
        if not refresh:
            cached = self._cache.load(device_id, config_number)
            if cached is not None:
                return [_convert_accessory(a, device_id) for a in cached]
        await self._await_pairing_reachable(device_id, timeout_s=20.0)
        try:
            await pairing.async_populate_accessories_state()
            raw = await pairing.list_accessories_and_characteristics()
        except Exception as exc:  # noqa: BLE001
            if cached := self._cache.load(device_id, 0):
                logger.warning(
                    "Live fetch of %s failed (%s); using cached layout",
                    device_id,
                    exc,
                )
                return [_convert_accessory(a, device_id) for a in cached]
            raise AccessoryNotFoundError(
                f"Cannot reach {device_id}: {exc}. "
                "For BLE devices: wake the accessory (button press) "
                "and retry; battery-powered devices may sleep for minutes."
            ) from exc
        self._cache.store(device_id, config_number, raw)
        return [_convert_accessory(a, device_id) for a in raw]

    async def _await_pairing_reachable(
        self, device_id: str, *, timeout_s: float = 20.0
    ) -> None:
        """Spin the discover loop until aiohomekit has seen a fresh advert.

        BLE accessories advert intermittently — by the time a caller wants to
        connect, the last advert from `homekit discover` may be stale. Run
        ``async_discover`` repeatedly so the underlying ``BleController``
        re-populates its ``discoveries`` cache before we attempt a connection.
        """
        ctrl = self._require_controller()
        target = device_id.upper()
        deadline = asyncio.get_event_loop().time() + max(timeout_s, 1.0)
        while True:
            async for discovery in ctrl.async_discover():
                d_id = self._device_id_for_discovery(discovery)
                if d_id and d_id.upper() == target:
                    return
            if asyncio.get_event_loop().time() >= deadline:
                return
            await asyncio.sleep(0.5)

    def _device_id_for_discovery(self, discovery: AbstractDiscovery) -> str | None:
        description = getattr(discovery, "description", None)
        if description is None:
            return None
        d_id = getattr(description, "id", None) or getattr(
            description, "device_id", None
        )
        return str(d_id).upper() if d_id else None

    async def read_characteristic(
        self, device_id: str, aid: int, iid: int
    ) -> Characteristic:
        pairing = await self._require_pairing(device_id)
        await self._await_pairing_reachable(device_id, timeout_s=20.0)
        kwargs = self._get_characteristics_kwargs(pairing)
        try:
            result = await pairing.get_characteristics([(aid, iid)], **kwargs)
        except (TimeoutError, AccessoryDisconnectedError) as exc:
            raise AccessoryNotFoundError(
                f"Cannot reach {device_id}: {exc}. "
                "Wake the accessory and retry."
            ) from exc
        raw = result.get((aid, iid))
        if raw is None:
            raise AccessoryNotFoundError(f"Characteristic {aid}.{iid} not returned")
        merged = dict(raw)
        merged.setdefault("aid", aid)
        merged.setdefault("iid", iid)
        return _convert_characteristic(merged, aid)

    async def write_characteristic(
        self, device_id: str, aid: int, iid: int, value: Any
    ) -> CharacteristicWriteResult:
        pairing = await self._require_pairing(device_id)
        await self._await_pairing_reachable(device_id, timeout_s=20.0)
        try:
            result = await pairing.put_characteristics([(aid, iid, value)])
        except (TimeoutError, AccessoryDisconnectedError) as exc:
            raise AccessoryNotFoundError(
                f"Cannot reach {device_id}: {exc}. "
                "Wake the accessory and retry."
            ) from exc
        failures = (result or {}).get((aid, iid))
        if failures:
            status = int(failures.get("status", -1))
            description = str(failures.get("description", ""))
            if status == -70404:
                raise CharacteristicNotWritableError(
                    f"Characteristic {aid}.{iid} is not writable"
                )
            return CharacteristicWriteResult(
                aid=aid,
                iid=iid,
                success=False,
                status=status,
                error=description or None,
            )
        return CharacteristicWriteResult(aid=aid, iid=iid, success=True)

    async def identify(self, device_id: str) -> None:
        pairing = await self._require_pairing(device_id)
        await pairing.identify()

    # ------------------------------------------------------------------ events

    async def subscribe(
        self, device_id: str, points: list[tuple[int, int]]
    ) -> AsyncIterator[HapEvent]:
        pairing = await self._require_pairing(device_id)
        await pairing.subscribe(points)
        queue: asyncio.Queue[HapEvent] = asyncio.Queue()

        def _push(payload: dict[Any, Any]) -> None:
            for key, value in payload.items():
                if not isinstance(key, tuple) or len(key) != 2:
                    continue
                aid, iid = int(key[0]), int(key[1])
                raw_value = value.get("value") if isinstance(value, dict) else value
                event = HapEvent(
                    device_id=device_id.upper(),
                    aid=aid,
                    iid=iid,
                    characteristic_type=None,
                    value=raw_value,
                    timestamp=dt.datetime.now(dt.UTC).isoformat(),
                )
                queue.put_nowait(event)

        disconnect = pairing.dispatcher_connect(_push)
        try:
            while True:
                yield await queue.get()
        finally:
            disconnect()
            try:
                await pairing.unsubscribe(points)
            except Exception as exc:  # noqa: BLE001
                logger.debug("unsubscribe failed: %s", exc)

    # ------------------------------------------------------------------ helpers

    def _require_controller(self) -> Controller:
        if self._controller is None:
            raise RuntimeError("Backend is not started")
        return self._controller

    async def _find_discovery(
        self, ctrl: Controller, device_id: str
    ) -> AbstractDiscovery:
        target = device_id.upper()
        try:
            return await ctrl.async_find(target, timeout=10)
        except Exception as exc:  # noqa: BLE001
            logger.debug("async_find miss for %s: %s", target, exc)
        deadline = asyncio.get_event_loop().time() + 30.0
        seen: set[str] = set()
        while asyncio.get_event_loop().time() < deadline:
            async for discovery in ctrl.async_discover(timeout=5):
                d_id = (
                    getattr(getattr(discovery, "description", None), "id", None)
                    or getattr(getattr(discovery, "description", None), "device_id", None)
                )
                if not d_id:
                    continue
                if str(d_id).upper() == target:
                    return discovery
                seen.add(str(d_id).upper())
            await asyncio.sleep(1)
        raise AccessoryNotFoundError(
            f"Accessory {device_id} not on the local network "
            f"(scanned IP + BLE for 30s; saw {len(seen)} other accessories)"
        )

    def _get_characteristics_kwargs(
        self, pairing: AbstractPairing
    ) -> dict[str, bool]:
        """Return the include_* kwargs supported by this pairing's transport.

        IP/CoAP pairings accept ``include_meta``/``include_perms``/...; BLE
        pairings only accept the bare characteristics list and reject the
        extra kwargs with ``TypeError``.
        """
        transport = getattr(pairing, "transport", None)
        transport_name = getattr(transport, "value", None) or str(transport or "")
        if "ble" in transport_name.lower():
            return {}
        return {"include_meta": True, "include_perms": True}

    async def _require_pairing(self, device_id: str) -> AbstractPairing:
        device = device_id.upper()
        pairing = self._pairings.get(device)
        if pairing is None:
            raise NotPairedError(f"No active pairing for {device}")
        return pairing

    def _device_id_for_pairing(self, pairing: AbstractPairing) -> str | None:
        pairing_id = getattr(pairing, "id", None)
        if pairing_id:
            return str(pairing_id).upper()
        description = getattr(pairing, "description", None)
        if description is None:
            return None
        device_id = getattr(description, "id", None) or getattr(
            description, "device_id", None
        )
        return str(device_id).upper() if device_id else None

    def _discovery_to_dataclass(
        self, discovery: AbstractDiscovery
    ) -> DiscoveredAccessory | None:
        description = getattr(discovery, "description", None)
        if description is None:
            return None
        device_id = getattr(description, "id", None) or getattr(
            description, "device_id", None
        )
        if not device_id:
            return None
        category = int(getattr(description, "category", 1) or 1)
        svc_type: str = getattr(description, "type", "") or ""
        if "_udp" in svc_type:
            transport: str = "thread"
        elif "_tcp" in svc_type:
            transport = "ip"
        else:
            transport = "ble"
        return DiscoveredAccessory(
            device_id=str(device_id).upper(),
            name=str(getattr(description, "name", "") or device_id),
            model=getattr(description, "model", None),
            host=str(getattr(description, "address", "") or ""),
            port=int(getattr(description, "port", 0) or 0),
            category=category,
            category_name=category_name(category),
            is_paired=bool(getattr(discovery, "paired", False)),
            config_number=int(getattr(description, "config_num", 0) or 0),
            is_bridge=category == 2,
            transport=transport,  # type: ignore[arg-type]
        )
