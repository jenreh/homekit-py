# Daemon Mode

## Overview

`homekit-py` can run a background daemon that holds a single `HomeKitClient`
instance and exposes it to CLI processes over a Unix-domain socket. This
avoids the overhead of re-pairing / re-connecting on every command invocation.

```text
┌────────────┐   Unix socket (RPC)   ┌────────────────┐
│  homekit   │ ─────────────────────▶│ homekit-daemon │
│   (CLI)    │ ◀──────────────────── │                │
└────────────┘                       │  HomeKitClient │
                                     │  (shared)      │
                                     └────────────────┘
```

When `daemon.enabled = true` (default) the CLI checks whether a daemon is
reachable on the configured socket. If not, it auto-spawns one (requires
`daemon.auto_spawn = true`), waits up to 5 s for it to become ready, then
proxies the request.

---

## Configuration

All options live under `[daemon]` in `~/.config/homekit-local/config.toml`
(or override via `HOMEKIT_DAEMON__*` environment variables):

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | Use the daemon when available |
| `auto_spawn` | `true` | Spawn the daemon automatically if not running |
| `socket_path` | *derived* | Unix socket path (defaults to `$XDG_RUNTIME_DIR/homekit.sock`) |
| `pid_path` | *derived* | PID file path |
| `log_path` | *derived* | Rotating log file path (1 MiB × 3) |
| `idle_timeout_s` | `600` | Shut down after N seconds with no connected clients (0 = never) |

---

## Starting and Stopping

The CLI manages the daemon transparently. You can also control it directly:

```sh
# Start manually
homekit-daemon [--socket-path PATH] [--log-path PATH] [--verbose]

# Check status
homekit daemon status

# Stop
homekit daemon stop
```

The daemon writes its PID to `pid_path` on startup and removes it on exit.
It honours `SIGINT` and `SIGTERM` for graceful shutdown.

---

## Wire Protocol

The CLI and daemon communicate over a **line-delimited JSON** protocol.
Each message is one UTF-8 JSON object terminated by `\n`, capped at 1 MiB.

### Message shapes

```text
request   {"id": N, "method": str, "params": {...}}
result    {"id": N, "result": <any>}
error     {"id": N, "error": {"code": str, "message": str}}
event     {"id": N, "event": <any>}   # streaming result frame
end       {"id": N, "end": true}      # final streaming frame
cancel    {"id": N, "cancel": true}   # client → server stream abort
```

`id` is a monotonically incrementing integer per connection. Multiple
requests can be in-flight simultaneously — responses are matched by `id`.

### Error codes

`error.code` is the exception class name, e.g. `NotPairedError`,
`AccessoryNotFoundError`. The client reconstructs the matching exception so
callers see the same exceptions as in direct mode.

---

## RPC Methods

All methods on `HomeKitClient` are exposed. Unary methods (one response):

| Method                  | Key params                                         |
|-------------------------|----------------------------------------------------|
| `discover`              | `timeout_s` (opt)                                  |
| `pair`                  | `device_id`, `pin`, `alias` (opt)                  |
| `unpair`                | `device_id`                                        |
| `list_pairings`         | —                                                  |
| `get_accessories`       | `device_id`, `refresh` (opt)                       |
| `identify`              | `device_id`                                        |
| `list_entities`         | `refresh` (opt)                                    |
| `get_entity`            | `entity_id`                                        |
| `get_state`             | `entity_id`, `refresh` (opt)                       |
| `get_characteristic`    | `device_id`, `aid`, `iid`                          |
| `put_characteristic`    | `device_id`, `aid`, `iid`, `value`                 |
| `turn_on`               | `entity_id`                                        |
| `turn_off`              | `entity_id`                                        |
| `set_brightness`        | `entity_id`, `value` (0–100)                       |
| `set_color_temperature` | `entity_id`, `kelvin`                              |
| `set_hue_saturation`    | `entity_id`, `hue`, `saturation`                   |
| `set_target_temperature`| `entity_id`, `celsius`                             |
| `set_target_mode`       | `entity_id`, `mode_id`                             |
| `set_lock`              | `entity_id`, `locked`, `confirmation_token` (opt)  |
| `set_position`          | `entity_id`, `percent`                             |
| `set_rotation_speed`    | `entity_id`, `percent`                             |

Streaming (server sends `event` frames then `end`):

| Method   | Params              | Event payload |
|----------|---------------------|---------------|
| `listen` | `entity_ids` (list) | `HapEvent`    |

---

## Concurrency and Locking

The server serialises I/O **per device**: two concurrent requests targeting
the same `device_id` queue behind an `asyncio.Lock`. Requests targeting
different devices run in parallel.

Entity-keyed methods (`turn_on`, `set_*`, etc.) are keyed on the `entity_id`
string rather than the underlying device ID at the dispatch layer.

---

## Idle Shutdown

When `idle_timeout_s > 0` the daemon checks every second whether any clients
are connected. If the connection count has been zero for longer than the
timeout it shuts down cleanly. Set to `0` to disable idle shutdown.
