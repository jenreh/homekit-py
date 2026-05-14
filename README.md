# homekit-py

Local control of Apple HomeKit accessories via the HomeKit Accessory Protocol
(HAP). Ships a Python library, a `homekit` CLI, and a `homekit-mcp` MCP server.

> HAP is cryptographically complex (SRP, Ed25519, Curve25519, ChaCha20-Poly1305,
> TLV8). This project delegates the protocol to `aiohomekit` and exposes a
> stable internal `HomeKitBackend` interface plus a high-level entity model.

## Install

```bash
uv sync
```

Python ≥ 3.14. Pairing material is stored via the OS keychain (`keyring`) with
an encrypted file fallback in `~/.config/homekit/pairings/`.

## Quick Start

```bash
# Discover accessories on the local network
uv run homekit discover

# Pair (one-time, requires the 8-digit setup PIN from the accessory)
uv run homekit pair AA:BB:CC:DD:EE:FF --pin 123-45-678 --alias "Living Room"

# List entities and current state
uv run homekit entities
uv run homekit get light.kitchen_ceiling

# Control
uv run homekit on light.kitchen_ceiling
uv run homekit brightness light.kitchen_ceiling 60
uv run homekit color-temp light.kitchen_ceiling 2700
uv run homekit temperature climate.hallway 21.5

# Watch events
uv run homekit watch light.kitchen_ceiling
```

## MCP Server

```bash
uv run homekit-mcp                       # STDIO transport (default)
uv run homekit-mcp --transport streamable-http --host 127.0.0.1 --port 8765
```

Claude Desktop / Claude Code:

```json
{
  "mcpServers": {
    "homekit": {
      "command": "uv",
      "args": ["run", "homekit-mcp"]
    }
  }
}
```

MCP is **read-only by default**. Set `[mcp].allow_write_tools = true` in
`~/.config/homekit/config.toml` to expose write tools. Dangerous operations
(`lock.unlock`, `garage.open`, `security_system.disarm`) require an explicit
`confirmation_token`.

## Configuration

`~/.config/homekit/config.toml` — see [docs/entity-model.md](docs/entity-model.md)
for the schema. Overrides via `HOMEKIT_CONFIG_DIR`, `HOMEKIT_PAIRING_DIR`.

## Docs

- [docs/pairing.md](docs/pairing.md) — pairing flow, key backup, recovery
- [docs/protocol.md](docs/protocol.md) — HAP primer, AID/IID, characteristic types
- [docs/entity-model.md](docs/entity-model.md) — service→domain mapping, registry
- [docs/troubleshooting.md](docs/troubleshooting.md) — mDNS, VLAN, connection limits

## License

MIT. See [LICENSE.md](LICENSE.md).
