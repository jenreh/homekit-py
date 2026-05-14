# Entity Model

HAP exposes raw services and characteristics. `homekit-py` builds an
**entity layer** on top so callers can address devices by stable IDs like
`light.kitchen_ceiling` rather than `(AID=1, IID=10)`.

## Entity IDs

Format: `{domain}.{slug}` where the slug is derived from the accessory's
`Name` characteristic, lower-cased, non-alphanumerics replaced with `_`.

On collisions a numeric suffix is appended:

```
light.bulb
light.bulb_2
light.bulb_3
```

## Service → Domain mapping

Defined in `SERVICE_DOMAIN_MAP` in
[homekit/core/registry.py](../homekit/core/registry.py).

| HAP Service           | Domain      |
|----------------------|-------------|
| Lightbulb            | `light` |
| Switch / Outlet      | `switch` |
| Thermostat           | `climate` |
| TemperatureSensor    | `sensor` |
| HumiditySensor       | `sensor` |
| MotionSensor         | `sensor` |
| ContactSensor        | `sensor` |
| LockMechanism        | `lock` |
| GarageDoorOpener     | `cover` |
| WindowCovering       | `cover` |
| Fan / FanV2          | `fan` |
| AirPurifier          | `fan` |
| SecuritySystem       | `security_system` |

Services not present in the map are intentionally **not** exposed as
entities — they remain visible via `homekit accessories <device-id>` so you
can still issue raw reads/writes.

## Capability

Each entity carries an `EntityCapability`:

```python
EntityCapability(
    domain="light",
    readable=frozenset({"On", "Brightness", "ColorTemperature"}),
    writable=frozenset({"On", "Brightness", "ColorTemperature"}),
    units={"Brightness": "percentage"},
    enum_values={},  # populated for LockState etc.
    safety_class="safe",  # safe | caution | dangerous
)
```

Domains map onto `safety_class` like so:

| Domain          | Safety class |
|----------------|-------------|
| `lock`         | `dangerous` |
| `security_system` | `dangerous` |
| `climate`      | `caution` |
| `cover`        | `caution` |
| everything else | `safe` |

## Overrides — `entities.toml`

Drop a `~/.config/homekit/entities.toml` to pin display names, rooms, or
aliases:

```toml
[entities."light.kitchen_ceiling"]
name = "Kitchen main light"
room = "Kitchen"
aliases = ["overhead", "ceiling lamp"]

[entities."climate.hallway"]
room = "Flur"
```

`homekit-py` will not invent rooms or scenes from the Apple Home database —
that data lives outside HAP. Maintain the registry yourself if you need
room-aware automations.
