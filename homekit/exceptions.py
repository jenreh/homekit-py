"""Domain exceptions for homekit-py."""

from __future__ import annotations


class HomeKitError(Exception):
    """Base class for all homekit-py errors."""


class PairingError(HomeKitError):
    """Pairing flow failed (wrong PIN, network error, accessory rejected)."""


class AlreadyPairedError(PairingError):
    """The accessory is already paired with another controller (`sf=0`)."""


class NotPairableError(PairingError):
    """The accessory is not currently advertising itself as pairable."""


class NotPairedError(HomeKitError):
    """The accessory has no stored pairing on this controller."""


class AccessoryNotFoundError(HomeKitError):
    """The requested device/accessory/entity could not be resolved."""


class ConnectionLimitError(HomeKitError):
    """The accessory rejected the connection because its limit is reached."""


class CharacteristicNotWritableError(HomeKitError):
    """The characteristic exists but its `perms` do not include `pw`."""


class PolicyBlockedError(HomeKitError):
    """An operation was blocked by the `dangerous_operations` policy."""


class PairingStoreCorruptError(HomeKitError):
    """The persisted pairing entry could not be parsed."""
