"""homekit-py — local control of Apple HomeKit accessories via HAP."""

from homekit.client import HomeKitClient
from homekit.core.models import (
    Accessory,
    AccessoryPairing,
    Characteristic,
    CharacteristicWriteResult,
    DiscoveredAccessory,
    Entity,
    EntityCapability,
    EntityState,
    HapEvent,
    Service,
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

__all__ = [
    "Accessory",
    "AccessoryNotFoundError",
    "AccessoryPairing",
    "AlreadyPairedError",
    "Characteristic",
    "CharacteristicNotWritableError",
    "CharacteristicWriteResult",
    "ConnectionLimitError",
    "DiscoveredAccessory",
    "Entity",
    "EntityCapability",
    "EntityState",
    "HapEvent",
    "HomeKitClient",
    "HomeKitError",
    "NotPairableError",
    "NotPairedError",
    "PairingError",
    "PairingStoreCorruptError",
    "PolicyBlockedError",
    "Service",
]
