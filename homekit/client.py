"""High-level entry point — ``HomeKitClient`` ties the pieces together."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any

from homekit.backends.aiohomekit_backend import AiohomekitBackend
from homekit.config import HomeKitConfig, load_config
from homekit.core.aliases import (
    SET_BRIGHTNESS,
    SET_COLOR_TEMPERATURE,
    SET_HUE,
    SET_LOCK,
    SET_ROTATION_SPEED,
    SET_SATURATION,
    SET_TARGET_HEATING_COOLING_STATE,
    SET_TARGET_POSITION,
    SET_TARGET_TEMPERATURE,
    TURN_ON_OFF,
    AliasSpec,
    clamp,
    coerce_bool,
    find_characteristic,
    kelvin_to_mirek,
)
from homekit.core.cache import AccessoryCache
from homekit.core.events import StateCache
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
from homekit.core.policy import Policy
from homekit.core.registry import build_entities, load_entity_overrides
from homekit.core.storage import PairingStore
from homekit.exceptions import AccessoryNotFoundError

if TYPE_CHECKING:
    from homekit.core.backend import HomeKitBackend

logger = logging.getLogger(__name__)


def _state_repr(value: object) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None:
        return "unknown"
    return str(value)


class HomeKitClient:
    """Composable facade — call ``await client.start()`` before any operations."""

    def __init__(
        self,
        *,
        config: HomeKitConfig | None = None,
        backend: HomeKitBackend | None = None,
    ) -> None:
        self._config = config or load_config()
        self._store = PairingStore(
            self._config.pairing_dir, backend=self._config.storage.backend
        )
        self._cache = AccessoryCache(
            self._config.cache_dir, ttl_seconds=self._config.cache.ttl_seconds
        )
        self._backend: HomeKitBackend = backend or AiohomekitBackend(
            self._store, self._cache
        )
        self._policy = Policy(dict(self._config.dangerous_operations))
        self._state_cache = StateCache()
        self._entities: dict[str, Entity] = {}
        self._accessory_index: dict[str, list[Accessory]] = {}
        self._started = False

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> HomeKitClient:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._started:
            return
        await self._backend.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        await self._backend.stop()
        self._started = False

    # ------------------------------------------------------------------ pass-through

    @property
    def config(self) -> HomeKitConfig:
        return self._config

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def state_cache(self) -> StateCache:
        return self._state_cache

    async def discover(self, timeout_s: float | None = None) -> list[DiscoveredAccessory]:
        timeout = timeout_s if timeout_s is not None else self._config.discovery.mdns_timeout_s
        return await self._backend.discover(timeout)

    async def pair(
        self, device_id: str, pin: str, alias: str | None = None
    ) -> AccessoryPairing:
        chosen_alias = alias or device_id
        return await self._backend.pair(device_id, pin, chosen_alias)

    async def unpair(self, device_id: str) -> None:
        await self._backend.unpair(device_id)

    async def list_pairings(self) -> list[AccessoryPairing]:
        return await self._backend.list_pairings()

    async def get_accessories(
        self, device_id: str, *, refresh: bool = False
    ) -> list[Accessory]:
        accessories = await self._backend.list_accessories(device_id, refresh=refresh)
        self._accessory_index[device_id.upper()] = accessories
        return accessories

    async def identify(self, device_id: str) -> None:
        await self._backend.identify(device_id)

    # ------------------------------------------------------------------ entities

    async def list_entities(self, *, refresh: bool = False) -> list[Entity]:
        overrides = load_entity_overrides(self._config.config_dir / "entities.toml")
        all_accessories: list[Accessory] = []
        pairings = await self.list_pairings()
        for pairing in pairings:
            accessories = await self.get_accessories(pairing.device_id, refresh=refresh)
            all_accessories.extend(accessories)
        entities = build_entities(all_accessories, overrides=overrides)
        self._entities = {e.entity_id: e for e in entities}
        return entities

    async def get_entity(self, entity_id: str) -> Entity:
        if entity_id not in self._entities:
            await self.list_entities()
        if entity_id not in self._entities:
            raise AccessoryNotFoundError(f"Unknown entity {entity_id}")
        return self._entities[entity_id]

    async def get_state(self, entity_id: str, *, refresh: bool = False) -> EntityState:
        entity = await self.get_entity(entity_id)
        cached = self._state_cache.get(entity_id) if not refresh else None
        if cached is not None and cached.fresh:
            return cached
        primary_char = self._primary_characteristic(entity)
        if primary_char is None:
            return self._state_cache.update(
                entity_id, state="unknown", attributes={}, source="poll"
            )
        live = await self._backend.read_characteristic(
            entity.device_id, primary_char.aid, primary_char.iid
        )
        attributes = await self._collect_attributes(entity)
        return self._state_cache.update(
            entity_id,
            state=_state_repr(live.value),
            attributes=attributes,
            source="poll",
        )

    # ------------------------------------------------------------------ characteristics (raw)

    async def get_characteristic(
        self, device_id: str, aid: int, iid: int
    ) -> Characteristic:
        return await self._backend.read_characteristic(device_id, aid, iid)

    async def put_characteristic(
        self, device_id: str, aid: int, iid: int, value: Any
    ) -> CharacteristicWriteResult:
        return await self._backend.write_characteristic(device_id, aid, iid, value)

    # ------------------------------------------------------------------ semantic shortcuts

    async def turn_on(self, entity_id: str) -> CharacteristicWriteResult:
        return await self._set_alias(entity_id, TURN_ON_OFF, True)

    async def turn_off(self, entity_id: str) -> CharacteristicWriteResult:
        return await self._set_alias(entity_id, TURN_ON_OFF, False)

    async def set_brightness(
        self, entity_id: str, value: float
    ) -> CharacteristicWriteResult:
        return await self._set_alias(entity_id, SET_BRIGHTNESS, round(value))

    async def set_color_temperature(
        self, entity_id: str, kelvin: int
    ) -> CharacteristicWriteResult:
        return await self._set_alias(
            entity_id, SET_COLOR_TEMPERATURE, kelvin_to_mirek(kelvin)
        )

    async def set_hue_saturation(
        self, entity_id: str, hue: float, saturation: float
    ) -> CharacteristicWriteResult:
        await self._set_alias(entity_id, SET_HUE, hue)
        return await self._set_alias(entity_id, SET_SATURATION, saturation)

    async def set_target_temperature(
        self, entity_id: str, celsius: float
    ) -> CharacteristicWriteResult:
        return await self._set_alias(entity_id, SET_TARGET_TEMPERATURE, float(celsius))

    async def set_target_mode(
        self, entity_id: str, mode_id: int
    ) -> CharacteristicWriteResult:
        return await self._set_alias(
            entity_id, SET_TARGET_HEATING_COOLING_STATE, int(mode_id)
        )

    async def set_lock(
        self, entity_id: str, locked: bool, *, confirmation_token: str | None = None
    ) -> CharacteristicWriteResult:
        action = "lock" if locked else "unlock"
        self._policy.enforce(f"lock.{action}", confirmation_token=confirmation_token)
        value = 1 if locked else 0
        return await self._set_alias(entity_id, SET_LOCK, value)

    async def set_position(
        self, entity_id: str, percent: int
    ) -> CharacteristicWriteResult:
        return await self._set_alias(entity_id, SET_TARGET_POSITION, int(percent))

    async def set_rotation_speed(
        self, entity_id: str, percent: int
    ) -> CharacteristicWriteResult:
        return await self._set_alias(entity_id, SET_ROTATION_SPEED, int(percent))

    # ------------------------------------------------------------------ events

    async def listen(
        self, entity_ids: Iterable[str] | None = None
    ) -> AsyncIterator[HapEvent]:
        import asyncio

        targets = list(entity_ids) if entity_ids else list(self._entities.keys())
        if not targets:
            await self.list_entities()
            targets = list(self._entities.keys())
        per_device: dict[str, list[tuple[int, int]]] = {}
        for entity_id in targets:
            entity = await self.get_entity(entity_id)
            char = self._primary_characteristic(entity)
            if char is None:
                continue
            per_device.setdefault(entity.device_id, []).append((char.aid, char.iid))
        queue: asyncio.Queue[HapEvent] = asyncio.Queue()

        async def _forward(device_id: str, points: list[tuple[int, int]]) -> None:
            stream = self._backend.subscribe(device_id, points)
            async for event in stream:
                await queue.put(event)

        tasks = [
            asyncio.create_task(_forward(device_id, points))
            for device_id, points in per_device.items()
        ]
        try:
            while True:
                yield await queue.get()
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                    logger.debug("event forwarder ended: %s", exc)

    # ------------------------------------------------------------------ helpers

    def _primary_characteristic(self, entity: Entity) -> Characteristic | None:
        accessories = self._accessory_index.get(entity.device_id, [])
        accessory = next((a for a in accessories if a.aid == entity.aid), None)
        if accessory is None:
            return None
        service = next(
            (s for s in accessory.services if s.iid == entity.service_iid), None
        )
        if service is None:
            return None
        order = {
            "light": "On",
            "switch": "On",
            "fan": "On",
            "lock": "LockCurrentState",
            "cover": "CurrentPosition",
            "climate": "CurrentTemperature",
            "sensor": _primary_sensor_characteristic(service.type_name),
            "security_system": "SecuritySystemCurrentState",
        }
        target = order.get(entity.domain)
        if target is None:
            return service.characteristics[0] if service.characteristics else None
        return service.get_characteristic(target)

    async def _collect_attributes(
        self, entity: Entity
    ) -> dict[str, bool | int | float | str | None]:
        accessories = self._accessory_index.get(entity.device_id, [])
        accessory = next((a for a in accessories if a.aid == entity.aid), None)
        if accessory is None:
            return {}
        service = next(
            (s for s in accessory.services if s.iid == entity.service_iid), None
        )
        if service is None:
            return {}
        attrs: dict[str, bool | int | float | str | None] = {}
        for char in service.characteristics:
            if char.type_name and char.readable:
                attrs[_attribute_key(char.type_name)] = char.value
        return attrs

    async def _set_alias(
        self,
        entity_id: str,
        spec: AliasSpec,
        value: Any,
    ) -> CharacteristicWriteResult:
        entity = await self.get_entity(entity_id)
        accessories = self._accessory_index.get(entity.device_id, [])
        accessory = next((a for a in accessories if a.aid == entity.aid), None)
        if accessory is None:
            accessories = await self.get_accessories(entity.device_id)
            accessory = next((a for a in accessories if a.aid == entity.aid), None)
        if accessory is None:
            raise AccessoryNotFoundError(
                f"Accessory {entity.device_id} aid={entity.aid} not found"
            )
        char = find_characteristic(accessory, spec)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = clamp(float(value), char)
            if char.format and char.format.lower() in {
                "uint8",
                "uint16",
                "uint32",
                "uint64",
                "int",
            }:
                value = int(value)
        elif spec.characteristic == "On" and not isinstance(value, bool):
            value = coerce_bool(value)
        return await self._backend.write_characteristic(
            entity.device_id, char.aid, char.iid, value
        )


_SENSOR_PRIMARY: dict[str, str] = {
    "TemperatureSensor": "CurrentTemperature",
    "HumiditySensor": "CurrentRelativeHumidity",
    "MotionSensor": "MotionDetected",
    "ContactSensor": "ContactSensorState",
    "LightSensor": "CurrentAmbientLightLevel",
    "OccupancySensor": "OccupancyDetected",
    "AirQualitySensor": "AirQuality",
    "CarbonDioxideSensor": "CarbonDioxideLevel",
    "CarbonMonoxideSensor": "CarbonMonoxideLevel",
    "LeakSensor": "LeakDetected",
    "SmokeSensor": "SmokeDetected",
    "BatteryService": "BatteryLevel",
}


def _primary_sensor_characteristic(service_type_name: str | None) -> str | None:
    if not service_type_name:
        return None
    return _SENSOR_PRIMARY.get(service_type_name)


def _attribute_key(type_name: str) -> str:
    return "".join(
        ["_" + c.lower() if c.isupper() and i else c.lower() for i, c in enumerate(type_name)]
    )


