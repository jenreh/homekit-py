# Troubleshooting

## "I can't see the accessory in `homekit discover`"

1. Confirm L2/L3 visibility:

   ```bash
   uv run homekit diagnose network
   uv run homekit diagnose mdns
   ```

2. mDNS does **not** cross VLANs by default. Either bridge `224.0.0.251`/UDP
   `5353` via an mDNS reflector (Avahi, `mdns-repeater`, OpenWrt's
   `umdns`), or move the controller and the accessory onto the same VLAN.
3. WiFi APs sometimes block multicast. Disable client isolation or enable
   IGMP-snooping with mDNS exceptions.
4. IPv6-only networks — some HomeKit accessories advertise on IPv4 only.
   Make sure the host has at least one IPv4 interface on the LAN.

## "Pairing fails with `AlreadyPairedError`"

The TXT record reports `sf=0`. The accessory is already paired with another
controller (most commonly Apple Home). Remove it from there first:

- iOS → Home → tap the accessory → settings → "Remove Accessory".

Then `homekit pair` again.

## "Pairing fails with `PairingError: Authentication`"

Wrong PIN. The setup code is printed on the device or its packaging. Format
with or without dashes (`12345678` or `123-45-678`).

## "Verbindungslimit erreicht"

Most accessories accept 8–16 simultaneous controllers. Run
`homekit pairings list` to see what you've registered; remove stale ones via
`homekit unpair`. Prefer the default `connection.mode = "ondemand"` so each
command opens and closes its own session.

## "State is stale in MCP responses"

The `EntityState` payload always includes `last_seen`, `source`, and
`fresh`. Treat `fresh=false` as untrusted. Causes:

- Event subscription dropped — the persistent connection died.
- Accessory rebooted (`c#` bumped, cache invalidated).
- Polling fallback hasn't fired yet (increase `event_poll_fallback_s`).

`homekit watch <entity>` will surface the next event as soon as it arrives;
use that to confirm push is working.

## "macOS keyring prompts every run"

The keyring backend stores the pairing material under the `homekit-py`
service. macOS will prompt the first time and remember the choice if you
allow it. If you're scripting, switch to file backend:

```toml
[storage]
backend = "file"
```

The file at `~/.config/homekit/pairings/pairings.json` remains chmod `0600`.

## "Linux: keyring not available"

Headless Linux has no DBus secret service by default. Either install
`gnome-keyring` / `KWallet` and unlock at login, or pick the file backend.

## Logs

All logging goes to **stderr** so stdout stays clean for JSON output:

```bash
uv run homekit -v get light.kitchen_ceiling 2>homekit.log
```

`-v` raises the level to `DEBUG`.

## Diagnostics summary

```bash
uv run homekit diagnose all
```

Runs every check and exits non-zero if any failed. Wire that into your
monitoring of choice.
