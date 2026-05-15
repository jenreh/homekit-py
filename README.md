# homekit-py

> Local control of Apple HomeKit accessories via the HomeKit Accessory Protocol (HAP) — Python library, CLI, and MCP server.

[![Python](https://img.shields.io/badge/python-3.14%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE.md)
![Tests](https://img.shields.io/badge/tests-88%20passing-brightgreen)
![Version](https://img.shields.io/badge/version-0.3.0-blue)

**homekit-py** talks directly to your accessories over the local network — no Apple cloud, no Apple ID, no internet required. HAP is cryptographically complex (SRP, Ed25519, Curve25519, ChaCha20-Poly1305, TLV8); this project delegates the wire protocol to `aiohomekit` and wraps it in a stable `HomeKitBackend` interface with a clean entity model.

---

## Features

- **Entity model** — lights, switches, sensors, locks, thermostats, covers, fans mapped to stable `domain.slug` IDs
- **Async Python library** — `async with HomeKitClient(config) as client: ...`
- **Rich CLI** — human-readable tables or `--json` for scripts
- **MCP server** — expose your accessories as tools to Claude or any MCP client
- **On-disk state cache** — fast repeated reads, configurable TTL
- **Dangerous-operations policy** — `lock.unlock`, `garage.open`, `security_system.disarm` gated by policy and confirmation tokens
- **mDNS discovery** — find all accessories on the LAN in seconds
- **HAP event subscriptions** — real-time characteristic change events via `homekit watch`

---

## Installation

```bash
pip install homekit-py
# or with uv
uv add homekit-py
```

Requires Python 3.14+. Pairing material is stored via the OS keychain (`keyring`) with an encrypted file fallback in `~/.config/homekit/pairings/`.

---

## Quick start

### 1. Discover accessories

```bash
homekit discover
```

```text
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Name               ┃ Device ID         ┃ Model           ┃ Category ┃ Host:Port       ┃ State  ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ Living Room Light  │ AA:BB:CC:DD:EE:FF │ Eve Light Strip │ Lighting │ 192.168.1.42:80 │ pairable│
└────────────────────┴───────────────────┴─────────────────┴──────────┴─────────────────┴────────┘
```

### 2. Pair the accessory

Enter the 8-digit PIN from the accessory's label or display:

```bash
homekit pair AA:BB:CC:DD:EE:FF --pin 123-45-678 --alias "Living Room"
```

Pairing data is saved to `~/.config/homekit/pairings/`. You only do this once.

### 3. Control

```bash
# List all entities
homekit entities

# Get current state
homekit get light.living_room

# Turn on / off
homekit on light.living_room
homekit off light.living_room

# Set brightness and colour temperature
homekit brightness light.living_room 60
homekit color-temp light.living_room 2700

# Set thermostat
homekit temperature climate.hallway 21.5

# Watch real-time events
homekit watch light.living_room
```

---

## CLI reference

```text
homekit [--verbose] <command>
```

| Command | Description |
| --- | --- |
| `homekit discover` | mDNS browse for advertised HomeKit accessories |
| `homekit pair DEVICE_ID --pin PIN` | Pair with an accessory (one-time) |
| `homekit unpair DEVICE_ID` | Remove a stored pairing |
| `homekit entities` | List all entities from paired accessories |
| `homekit entity ENTITY_ID` | Show capability and state for one entity |
| `homekit get ENTITY_ID` | Fetch current state |
| `homekit set ENTITY_ID EXPR` | Set state or attribute (`on`, `brightness=70`) |
| `homekit on ENTITY_ID` | Turn on |
| `homekit off ENTITY_ID` | Turn off |
| `homekit brightness ENTITY_ID VALUE` | Set brightness (0–100) |
| `homekit color-temp ENTITY_ID KELVIN` | Set colour temperature |
| `homekit temperature ENTITY_ID CELSIUS` | Set thermostat target |
| `homekit lock ENTITY_ID` | Lock a lock entity |
| `homekit watch ENTITY_ID` | Stream real-time state changes |
| `homekit pairings list` | List stored pairings |
| `homekit pairings export --out FILE` | Back up pairing store to JSON |
| `homekit pairings import FILE` | Restore pairings from a JSON backup |
| `homekit diagnose mdns` | Check mDNS / Bonjour health |
| `homekit diagnose network` | Check network reachability |
| `homekit diagnose storage` | Verify pairing-store integrity |
| `homekit raw get DEVICE_ID AID IID` | Read a raw HAP characteristic |
| `homekit raw set DEVICE_ID AID IID VAL` | Write a raw HAP characteristic |

Use `--json` for machine-readable output:

```bash
homekit --json entities | jq '.[].entity_id'
```

---

## Python library

```python
import asyncio
from homekit import HomeKitClient, load_config


async def main():
    async with HomeKitClient(load_config()) as client:
        # List all entities
        for entity in await client.list_entities():
            print(entity.entity_id, entity.domain, entity.name)

        # Read state
        state = await client.get_state("light.living_room", refresh=True)
        print(state.state, state.attributes)

        # Control
        await client.turn_on("light.living_room")
        await client.set_brightness("light.living_room", 60.0)
        await client.set_color_temperature("light.living_room", 2700)
        await client.set_target_temperature("climate.hallway", 21.5)


asyncio.run(main())
```

---

## MCP server

**homekit-py** ships with an MCP server that exposes your accessories as tools for Claude or any MCP-compatible client.

```bash
homekit-mcp                                          # STDIO (default)
homekit-mcp --transport streamable-http --host 127.0.0.1 --port 8765
```

> [!WARNING]
> The MCP server is **read-only by default**. Set `[mcp].allow_write_tools = true` in `~/.config/homekit/config.toml` to expose write tools.

### Claude Desktop

```json
{
  "mcpServers": {
    "homekit": {
      "command": "homekit-mcp"
    }
  }
}
```

### VS Code (agent mode)

```json
{
  "mcp": {
    "servers": {
      "homekit": {
        "command": "homekit-mcp",
        "type": "stdio"
      }
    }
  }
}
```

### Available MCP tools

#### Read (always available)

`homekit_list_entities` · `homekit_get_state` · `homekit_identify`

#### Write (requires `allow_write_tools = true`)

`homekit_set_light` · `homekit_set_switch` · `homekit_set_climate` · `homekit_set_cover` · `homekit_lock` · `homekit_unlock`

#### Resources

`homekit://devices` · `homekit://devices/{device_id}` · `homekit://entities` · `homekit://entities/{entity_id}` · `homekit://state/{entity_id}` · `homekit://capabilities/{entity_id}` · `homekit://events/recent`

---

## Configuration

Config file: `~/.config/homekit/config.toml`

```toml
[controller]
name = "homekit-local"

[discovery]
mdns_timeout_s = 5.0
ip_only = false

[connection]
mode = "ondemand"          # "ondemand" | "persistent"
request_timeout_s = 10.0

[cache]
ttl_seconds = 3600

[storage]
backend = "keyring"        # "keyring" | "file"

[mcp]
allow_write_tools = false
allow_raw_characteristic_writes = false
audit_log = true

[dangerous_operations]
"lock.unlock" = "confirmation_required"
"garage.open" = "disabled"
"security_system.disarm" = "disabled"
"cover.open" = "allow"
```

Environment variable overrides:

| Variable | Overrides |
| --- | --- |
| `HOMEKIT_CONFIG_DIR` | config directory path |
| `HOMEKIT_PAIRING_DIR` | pairing store directory |
| `HOMEKIT_CONNECTION__REQUEST_TIMEOUT_S` | `connection.request_timeout_s` |
| `HOMEKIT_MCP__ALLOW_WRITE_TOOLS` | `mcp.allow_write_tools` |

---

## Dangerous operations policy

Certain operations are gated to prevent accidental or unauthorised control:

| Policy | Behaviour |
| --- | --- |
| `allow` | Executes immediately |
| `confirmation_required` | Requires a `confirmation_token` argument |
| `disabled` | Always rejected |

Default: `lock.unlock` → `confirmation_required`, `garage.open` and `security_system.disarm` → `disabled`.

---

## Docs

- [docs/pairing.md](docs/pairing.md) — pairing flow, key backup, recovery
- [docs/protocol.md](docs/protocol.md) — HAP primer, AID/IID, characteristic types
- [docs/entity-model.md](docs/entity-model.md) — service→domain mapping, registry
- [docs/troubleshooting.md](docs/troubleshooting.md) — mDNS, VLAN, connection limits

---

## Development

```bash
git clone https://github.com/jenreh/homekit-py
cd homekit-py
uv sync
task test     # pytest with coverage
task lint     # ruff + mypy
task format   # ruff format
```

> [!NOTE]
> A `FakeBackend` simulator (`tests/fake_backend.py`) is included for use in tests. It stubs the `HomeKitBackend` interface without requiring real accessories or network access.
