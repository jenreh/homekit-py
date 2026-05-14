# HAP Protocol Primer

The HomeKit Accessory Protocol (HAP) is Apple's proprietary local protocol
for controlling smart-home accessories. `homekit-py` does not re-implement
HAP; it delegates the cryptography and framing to `aiohomekit`. This page
exists so the reader understands what's happening underneath.

## Transport

```
┌─────────────────────────────────────────┐
│ Application: GET /accessories,          │
│              PUT /characteristics, …    │
├─────────────────────────────────────────┤
│ HTTP/1.1 framing (HAP-JSON)            │
├─────────────────────────────────────────┤
│ ChaCha20-Poly1305 session encryption    │
├─────────────────────────────────────────┤
│ TCP — port advertised via mDNS          │
└─────────────────────────────────────────┘
```

`aiohomekit` derives the session key after a *Pair-Verify* exchange on every
TCP connection. Standard HTTP libraries cannot speak this — the encryption
happens **below** the HTTP framing layer.

## Identifiers

- **AID** (Accessory Instance ID): the device. `1` for non-bridged
  accessories; bridges expose multiple AIDs (one per bridged device).
- **IID** (Instance ID): every service and every characteristic has its own
  IID, scoped to the AID. IIDs are stable for the lifetime of a config_number
  (`c#`).

## Data model

```
Accessory (aid)
├── Service (iid, type UUID, "primary" flag)
│   ├── Characteristic (iid, type UUID, value, format, perms, unit, min/max/step)
│   └── …
└── …
```

The fundamental identifiers are 128-bit UUIDs; their first 8 hex characters
form the well-known "short form" (e.g. `00000043` ≡ `Lightbulb`).
`homekit-py` resolves UUIDs to human names via `aiohomekit`'s type tables.

## Important service types

| Service              | Short UUID  | Domain mapping |
|---------------------|-------------|----------------|
| Lightbulb           | `00000043`  | `light` |
| Switch              | `00000049`  | `switch` |
| Outlet              | `00000047`  | `switch` |
| Thermostat          | `0000004A`  | `climate` |
| LockMechanism       | `00000045`  | `lock` |
| WindowCovering      | `0000008C`  | `cover` |
| Fan / FanV2         | `00000040`  | `fan` |
| TemperatureSensor   | `0000008A`  | `sensor` |

Full mapping: [homekit/core/registry.py](../homekit/core/registry.py).

## Permissions

Characteristics carry a `perms` list:

- `pr` — paired-read
- `pw` — paired-write
- `ev` — supports events (push notifications)
- `aa`, `tw`, `hd`, `wr` — less common

`homekit-py` refuses to call `write_characteristic` on a characteristic
without `pw` (see `CharacteristicNotWritableError`).

## Push events

Subscribing to a characteristic via `GET /characteristics?id=AID.IID&ev=1`
turns the same TCP connection into an event channel; the accessory writes
asynchronous notifications down it. `homekit-py` exposes that with
`HomeKitClient.listen(entity_ids)` which yields `HapEvent` objects.

## Config number (`c#`)

The mDNS TXT record carries `c#`, incremented every time the accessory's
configuration changes (a new service appears, a name changes, etc.). The
`AccessoryCache` keys on `c#`, so an old cached layout is automatically
discarded when the accessory restructures itself.
