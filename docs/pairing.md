# Pairing

HomeKit pairing is **one-time per controller**. Once `homekit pair` succeeds,
the Ed25519 key material is stored locally and is reused on every subsequent
session via the HAP *Pair-Verify* handshake.

> ⚠️ **Losing the pairing material is irreversible.** To re-pair you must
> physically reset the accessory (vendor-specific procedure). Back up
> `~/.config/homekit/pairings/pairings.json` regularly.

## Prerequisites

- Accessory must be **on the same L2/L3 broadcast domain** as this host
  (mDNS / `_hap._tcp.local.`).
- Accessory must currently advertise `sf=1` (pairable). If `sf=0`, it is
  already paired with another controller (Apple Home, another instance, etc.)
  and you must remove that pairing first.

```bash
uv run homekit discover
```

If the accessory shows up but the row reads `paired`, you cannot pair it
again without resetting it.

## Pair an accessory

```bash
uv run homekit pair AA:BB:CC:DD:EE:FF \
    --pin 123-45-678 \
    --alias "Living Room Hue"
```

The 8-digit PIN comes from the device sticker or its packaging. Format with
or without dashes — both are accepted.

On success the pairing is persisted to:

- `~/.config/homekit/pairings/pairings.json` (the canonical aiohomekit blob)
- the OS keychain under service `homekit-py`, key `pairings`, when
  `[storage].backend = "keyring"` (the default).

## Listing and removing pairings

```bash
uv run homekit pairings list
uv run homekit unpair AA:BB:CC:DD:EE:FF
```

`unpair` calls the HAP `/pairings` endpoint to remove the controller from the
accessory's allow-list, then deletes the local pairing record. Run this
before disposing of an accessory.

## Backup and restore

```bash
uv run homekit pairings export --out ~/backups/homekit-pairings.json
uv run homekit pairings import ~/backups/homekit-pairings.json
```

The exported file contains private Ed25519 keys. Store it like a password —
encrypted backup, restricted access, off-site copy.

## Recovering from a lost pairing

There is no way to recover Ed25519 material without the backup. If the
keychain is wiped and you have no export, you have to:

1. Factory-reset the accessory (vendor instructions).
2. Run `homekit pair` again with the new setup code.

`homekit diagnose pairability` will tell you which accessories are still
pairable on the LAN — useful when triaging "the device disappeared from my
controller" issues.
