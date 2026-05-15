"""Typer-based CLI — entity-first commands plus raw access and diagnostics."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from homekit.cli import exit_codes as exits
from homekit.client import HomeKitClient
from homekit.config import load_config
from homekit.daemon.client import DaemonRpcClient, RemoteHomeKitClient
from homekit.daemon.lifecycle import ensure_running, stop_daemon
from homekit.daemon.lifecycle import status as daemon_status
from homekit.daemon.protocol import json_default
from homekit.diagnostics.mcp_security import check_mcp_security
from homekit.diagnostics.mdns import check_mdns
from homekit.diagnostics.network import check_network
from homekit.diagnostics.pairability import check_pairability
from homekit.diagnostics.storage import check_storage
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

logger = logging.getLogger(__name__)

console = Console(stderr=False, emoji=False)
err_console = Console(stderr=True, emoji=False)


app = typer.Typer(
    no_args_is_help=True,
    add_completion=True,
    help="homekit — local control of Apple HomeKit accessories via HAP",
)
pairings_app = typer.Typer(help="Manage stored pairings")
diagnose_app = typer.Typer(help="Run diagnostic checks")
raw_app = typer.Typer(help="Raw characteristic read/write")
daemon_app = typer.Typer(help="Manage the homekit-py background daemon")
app.add_typer(pairings_app, name="pairings")
app.add_typer(diagnose_app, name="diagnose")
app.add_typer(raw_app, name="raw")
app.add_typer(daemon_app, name="daemon")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        stream=sys.stderr, level=level, format="%(levelname)s %(name)s %(message)s"
    )


def _print_json(payload: Any) -> None:
    console.print_json(json.dumps(payload, default=json_default))


def _exit_for(exc: Exception) -> int:
    if isinstance(exc, AlreadyPairedError):
        return exits.PAIRING_FAILED
    if isinstance(exc, NotPairableError):
        return exits.PAIRING_FAILED
    if isinstance(exc, PairingError):
        return exits.PAIRING_FAILED
    if isinstance(exc, NotPairedError):
        return exits.NOT_PAIRED
    if isinstance(exc, PairingStoreCorruptError):
        return exits.PAIRING_STORE_CORRUPT
    if isinstance(exc, ConnectionLimitError):
        return exits.CONNECTION_LIMIT
    if isinstance(exc, CharacteristicNotWritableError):
        return exits.CHARACTERISTIC_INVALID
    if isinstance(exc, PolicyBlockedError):
        return exits.POLICY_BLOCKED
    if isinstance(exc, AccessoryNotFoundError):
        return exits.ACCESSORY_UNREACHABLE
    if isinstance(exc, HomeKitError):
        return exits.ACCESSORY_UNREACHABLE
    return exits.USAGE


def _run(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except HomeKitError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=_exit_for(exc)) from exc


_NO_DAEMON: bool = False


async def _with_client(action: Any) -> Any:
    config = load_config()
    if config.daemon.enabled and not _NO_DAEMON:
        ok = await ensure_running(
            config.daemon.socket_path,
            auto_spawn=config.daemon.auto_spawn,
            log_path=config.daemon.log_path,
        )
        if ok:
            rpc = DaemonRpcClient(config.daemon.socket_path)
            try:
                await rpc.connect()
                return await action(RemoteHomeKitClient(rpc))
            finally:
                await rpc.close()
        logger.warning("Daemon unreachable; falling back to in-process mode")
    async with HomeKitClient(config=config) as client:
        return await action(client)


# ------------------------------------------------------------------ root options


@app.callback()
def _root(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
    no_daemon: bool = typer.Option(
        False, "--no-daemon", help="Bypass the daemon and run in-process for this call"
    ),
) -> None:
    global _NO_DAEMON
    _NO_DAEMON = no_daemon
    ctx.obj = {"verbose": verbose, "no_daemon": no_daemon}
    _setup_logging(verbose)


# ------------------------------------------------------------------ discovery


@app.command("discover")
def cmd_discover(
    timeout: float = typer.Option(
        0.0,
        "--timeout",
        help=(
            "Browse duration in seconds. 0 = use [discovery].mdns_timeout_s. "
            "Battery-powered BLE accessories may need 20–60 s."
        ),
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table"),
) -> None:
    """Browse mDNS for advertised HomeKit accessories."""

    async def _action(client: HomeKitClient) -> list[Any]:
        return await client.discover(timeout if timeout > 0 else None)

    accessories = _run(_with_client(_action))
    if as_json:
        _print_json(accessories)
        return
    table = Table(title="HomeKit accessories on the LAN")
    for column in (
        "Name",
        "Device ID",
        "Model",
        "Category",
        "Transport",
        "Host:Port",
        "State",
    ):
        table.add_column(column)
    for accessory in accessories:
        state = "paired" if accessory.is_paired else "pairable"
        transport = getattr(accessory, "transport", "ip")
        host_port = f"{accessory.host}:{accessory.port}" if accessory.host else "—"
        table.add_row(
            accessory.name,
            accessory.device_id,
            accessory.model or "—",
            accessory.category_name,
            transport,
            host_port,
            state,
        )
    console.print(table)


# ------------------------------------------------------------------ pairing


@app.command("pair")
def cmd_pair(
    device_id: str = typer.Argument(..., help="Accessory device ID"),
    pin: str = typer.Option(..., "--pin", help="8-digit setup code, e.g. 123-45-678"),
    alias: str = typer.Option(
        None, "--alias", help="Friendly name (defaults to device ID)"
    ),
) -> None:
    """Pair with an accessory (one-time per controller)."""

    async def _action(client: HomeKitClient) -> Any:
        return await client.pair(device_id, pin, alias=alias)

    result = _run(_with_client(_action))
    _print_json(result)


@app.command("unpair")
def cmd_unpair(device_id: str) -> None:
    """Remove a stored pairing."""

    async def _action(client: HomeKitClient) -> None:
        await client.unpair(device_id)

    _run(_with_client(_action))
    console.print(f"Removed pairing for {device_id}")


@pairings_app.command("list")
def cmd_pairings_list(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List stored pairings."""

    async def _action(client: HomeKitClient) -> list[Any]:
        return await client.list_pairings()

    pairings = _run(_with_client(_action))
    if as_json:
        _print_json(pairings)
        return
    table = Table(title="Stored pairings")
    for column in ("Device ID", "Alias", "Host:Port", "Paired at"):
        table.add_column(column)
    for pairing in pairings:
        table.add_row(
            pairing.device_id,
            pairing.name,
            f"{pairing.host}:{pairing.port}" if pairing.host else "—",
            pairing.paired_at or "—",
        )
    console.print(table)


@pairings_app.command("export")
def cmd_pairings_export(
    out: Path = typer.Option(..., "--out", help="Destination JSON file"),
) -> None:
    """Export the pairing-store JSON for backup."""
    config = load_config()
    src = config.pairing_dir / "pairings.json"
    if not src.exists():
        err_console.print(f"[yellow]Nothing to export: {src} does not exist[/yellow]")
        raise typer.Exit(code=exits.NOT_PAIRED)
    out.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    console.print(f"Wrote {out}")


@pairings_app.command("import")
def cmd_pairings_import(
    source: Path = typer.Argument(..., help="JSON file produced by `pairings export`"),
) -> None:
    """Restore pairings from a JSON backup."""
    config = load_config()
    dst = config.pairing_dir / "pairings.json"
    payload = source.read_text(encoding="utf-8")
    try:
        json.loads(payload)
    except json.JSONDecodeError as exc:
        err_console.print(f"[red]Invalid JSON:[/red] {exc}")
        raise typer.Exit(code=exits.USAGE) from exc
    dst.write_text(payload, encoding="utf-8")
    console.print(f"Imported {source} → {dst}")


# ------------------------------------------------------------------ entities


@app.command("entities")
def cmd_entities(as_json: bool = typer.Option(False, "--json")) -> None:
    """List entities derived from all paired accessories."""

    async def _action(client: HomeKitClient) -> list[Any]:
        return await client.list_entities()

    entities = _run(_with_client(_action))
    if as_json:
        _print_json(entities)
        return
    table = Table(title="Entities")
    for column in ("Entity ID", "Domain", "Name", "Device", "AID/IID", "Safety"):
        table.add_column(column)
    for entity in entities:
        table.add_row(
            entity.entity_id,
            entity.domain,
            entity.name,
            entity.device_id,
            f"{entity.aid}/{entity.service_iid}",
            entity.capability.safety_class,
        )
    console.print(table)


@app.command("entity")
def cmd_entity(
    entity_id: str,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show capability and current state for a single entity."""

    async def _action(client: HomeKitClient) -> dict[str, Any]:
        await client.list_entities()
        entity = await client.get_entity(entity_id)
        state = await client.get_state(entity_id)
        return {"entity": entity, "state": state}

    payload = _run(_with_client(_action))
    if as_json:
        _print_json(payload)
        return
    entity = payload["entity"]
    state = payload["state"]
    console.print(
        f"[bold]{entity.entity_id}[/bold]  domain={entity.domain} "
        f"name={entity.name}  device={entity.device_id}"
    )
    console.print(f"  state: {state.state} (source={state.source} fresh={state.fresh})")
    if state.attributes:
        console.print(f"  attributes: {state.attributes}")
    cap = entity.capability
    console.print(f"  readable: {sorted(cap.readable)}")
    console.print(f"  writable: {sorted(cap.writable)}")
    console.print(f"  safety: {cap.safety_class}")


@app.command("get")
def cmd_get(
    entity_id: str,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch the current state of an entity."""

    async def _action(client: HomeKitClient) -> Any:
        await client.list_entities()
        return await client.get_state(entity_id, refresh=True)

    state = _run(_with_client(_action))
    if as_json:
        _print_json(state)
        return
    console.print(f"{state.entity_id}: {state.state}")


# ------------------------------------------------------------------ semantic set


def _resolve_lock_value(action: str) -> bool:
    return action.lower() in {"lock", "true", "1", "locked", "secured"}


@app.command("set")
def cmd_set(
    entity_id: str,
    expression: str = typer.Argument(
        ..., help="Either a state (`on`, `off`) or `key=value` (`brightness=70`)"
    ),
) -> None:
    """Set a property on an entity. Examples: `homekit set light.kitchen on`,
    `homekit set light.kitchen brightness=70`."""

    async def _action(client: HomeKitClient) -> Any:
        await client.list_entities()
        entity = await client.get_entity(entity_id)
        if "=" in expression:
            key, raw = expression.split("=", 1)
            return await _dispatch_set(client, entity_id, entity.domain, key, raw)
        return await _dispatch_set(
            client, entity_id, entity.domain, "state", expression
        )

    result = _run(_with_client(_action))
    _print_json(result)


async def _dispatch_set(
    client: HomeKitClient, entity_id: str, domain: str, key: str, raw: str
) -> Any:
    if key == "state":
        if raw.lower() in {"on", "true", "1", "open"}:
            return await client.turn_on(entity_id)
        if raw.lower() in {"off", "false", "0", "closed"}:
            return await client.turn_off(entity_id)
        raise typer.BadParameter(f"Unknown state {raw!r}")
    if key == "brightness":
        return await client.set_brightness(entity_id, float(raw))
    if key in {"color_temp", "color_temperature", "kelvin"}:
        return await client.set_color_temperature(entity_id, int(raw))
    if key in {"target_temperature", "temperature"}:
        return await client.set_target_temperature(entity_id, float(raw))
    if key == "mode":
        return await client.set_target_mode(entity_id, int(raw))
    if key in {"position", "target_position"}:
        return await client.set_position(entity_id, int(raw))
    if key == "rotation_speed":
        return await client.set_rotation_speed(entity_id, int(raw))
    if key == "locked":
        locked = raw.lower() in {"true", "1", "locked", "yes"}
        return await client.set_lock(entity_id, locked)
    raise typer.BadParameter(f"Unknown attribute {key!r} for domain {domain!r}")


# ------------------------------------------------------------------ verb shortcuts


def _shortcut(name: str, method: str):  # type: ignore[no-untyped-def]
    @app.command(name)
    def _cmd(entity_id: str) -> None:
        async def _action(client: HomeKitClient) -> Any:
            await client.list_entities()
            return await getattr(client, method)(entity_id)

        _print_json(_run(_with_client(_action)))

    _cmd.__name__ = f"cmd_{name.replace('-', '_')}"
    return _cmd


_shortcut("on", "turn_on")
_shortcut("off", "turn_off")


@app.command("brightness")
def cmd_brightness(entity_id: str, value: float) -> None:
    async def _action(client: HomeKitClient) -> Any:
        await client.list_entities()
        return await client.set_brightness(entity_id, value)

    _print_json(_run(_with_client(_action)))


@app.command("color-temp")
def cmd_color_temp(entity_id: str, kelvin: int) -> None:
    async def _action(client: HomeKitClient) -> Any:
        await client.list_entities()
        return await client.set_color_temperature(entity_id, kelvin)

    _print_json(_run(_with_client(_action)))


@app.command("temperature")
def cmd_temperature(entity_id: str, celsius: float) -> None:
    async def _action(client: HomeKitClient) -> Any:
        await client.list_entities()
        return await client.set_target_temperature(entity_id, celsius)

    _print_json(_run(_with_client(_action)))


@app.command("lock")
def cmd_lock(
    entity_id: str,
    confirmation_token: str | None = typer.Option(
        None, "--confirm", help="Confirmation token (only required by policy)"
    ),
) -> None:
    async def _action(client: HomeKitClient) -> Any:
        await client.list_entities()
        return await client.set_lock(
            entity_id, True, confirmation_token=confirmation_token
        )

    _print_json(_run(_with_client(_action)))


@app.command("unlock")
def cmd_unlock(
    entity_id: str,
    confirmation_token: str | None = typer.Option(
        None,
        "--confirm",
        help="Confirmation token (lock.unlock requires it by default)",
    ),
) -> None:
    async def _action(client: HomeKitClient) -> Any:
        await client.list_entities()
        return await client.set_lock(
            entity_id, False, confirmation_token=confirmation_token
        )

    _print_json(_run(_with_client(_action)))


@app.command("position")
def cmd_position(entity_id: str, percent: int) -> None:
    async def _action(client: HomeKitClient) -> Any:
        await client.list_entities()
        return await client.set_position(entity_id, percent)

    _print_json(_run(_with_client(_action)))


@app.command("identify")
def cmd_identify(device_id: str) -> None:
    """Blink the accessory identified by its device ID."""

    async def _action(client: HomeKitClient) -> None:
        await client.identify(device_id)

    _run(_with_client(_action))
    console.print(f"Identify sent to {device_id}")


# ------------------------------------------------------------------ watch


@app.command("watch")
def cmd_watch(entity_ids: list[str] = typer.Argument(None)) -> None:
    """Stream events for entity IDs (or all entities if none given)."""

    async def _action(client: HomeKitClient) -> None:
        await client.list_entities()
        async for event in client.listen(entity_ids or None):
            console.print(
                f"[cyan]{event.timestamp}[/cyan] "
                f"{event.device_id} aid={event.aid} iid={event.iid} -> {event.value}"
            )

    _run(_with_client(_action))


# ------------------------------------------------------------------ raw access


@raw_app.command("read")
def cmd_raw_read(device_id: str, aid: int, iid: int) -> None:
    async def _action(client: HomeKitClient) -> Any:
        return await client.get_characteristic(device_id, aid, iid)

    _print_json(_run(_with_client(_action)))


@raw_app.command("write")
def cmd_raw_write(device_id: str, aid: int, iid: int, value: str) -> None:
    config = load_config()
    if not config.mcp.allow_raw_characteristic_writes:
        err_console.print(
            "[red]raw write disabled:[/red] set "
            "[mcp].allow_raw_characteristic_writes = true to enable"
        )
        raise typer.Exit(code=exits.POLICY_BLOCKED)
    try:
        coerced: Any = json.loads(value)
    except json.JSONDecodeError:
        coerced = value

    async def _action(client: HomeKitClient) -> Any:
        return await client.put_characteristic(device_id, aid, iid, coerced)

    _print_json(_run(_with_client(_action)))


# ------------------------------------------------------------------ accessories


@app.command("accessories")
def cmd_accessories(
    device_id: str, as_json: bool = typer.Option(False, "--json")
) -> None:
    async def _action(client: HomeKitClient) -> Any:
        return await client.get_accessories(device_id, refresh=True)

    accessories = _run(_with_client(_action))
    if as_json:
        _print_json(accessories)
        return
    for accessory in accessories:
        console.print(f"[bold]{accessory.name}[/bold] aid={accessory.aid}")
        for service in accessory.services:
            console.print(f"  service iid={service.iid} type={service.type_name}")
            for char in service.characteristics:
                console.print(
                    f"    char iid={char.iid} {char.type_name} = {char.value!r} "
                    f"perms={list(char.perms)} unit={char.unit}"
                )


# ------------------------------------------------------------------ diagnostics


def _emit_result(result: Any) -> None:
    icon = "✅" if result.passed else "❌"
    console.print(f"{icon} [{result.name}] {result.details}")


@diagnose_app.command("mdns")
def cmd_diag_mdns() -> None:
    result = asyncio.run(check_mdns())
    _emit_result(result)


@diagnose_app.command("network")
def cmd_diag_network() -> None:
    _emit_result(check_network())


@diagnose_app.command("pairability")
def cmd_diag_pairability() -> None:
    result = asyncio.run(check_pairability())
    _emit_result(result)


@diagnose_app.command("storage")
def cmd_diag_storage() -> None:
    _emit_result(check_storage(load_config()))


@diagnose_app.command("mcp-security")
def cmd_diag_mcp_security() -> None:
    _emit_result(check_mcp_security(load_config()))


@diagnose_app.command("all")
def cmd_diag_all() -> None:
    """Run every diagnostic and exit non-zero if any failed."""
    config = load_config()
    failures = 0
    for result in [
        asyncio.run(check_mdns()),
        check_network(),
        asyncio.run(check_pairability()),
        check_storage(config),
        check_mcp_security(config),
    ]:
        _emit_result(result)
        if not result.passed:
            failures += 1
    if failures:
        raise typer.Exit(code=exits.ACCESSORY_UNREACHABLE)


# ------------------------------------------------------------------ daemon control


@daemon_app.command("status")
def cmd_daemon_status() -> None:
    """Show whether the background daemon is reachable."""
    cfg = load_config()
    st = daemon_status(cfg.daemon.socket_path, cfg.daemon.pid_path)
    if st.running:
        console.print(
            f"[green]running[/green]  socket={st.socket_path}"
            + (f"  pid={st.pid}" if st.pid else "")
        )
        return
    console.print(
        f"[yellow]not running[/yellow]  socket={st.socket_path}  ({st.detail})"
    )
    raise typer.Exit(code=1)


@daemon_app.command("start")
def cmd_daemon_start() -> None:
    """Ensure a daemon is running (auto-spawn if needed)."""
    cfg = load_config()

    async def _go() -> bool:
        return await ensure_running(
            cfg.daemon.socket_path,
            auto_spawn=True,
            log_path=cfg.daemon.log_path,
        )

    if asyncio.run(_go()):
        console.print(f"Daemon ready at {cfg.daemon.socket_path}")
        return
    err_console.print("[red]Daemon failed to start[/red]")
    raise typer.Exit(code=exits.ACCESSORY_UNREACHABLE)


@daemon_app.command("stop")
def cmd_daemon_stop() -> None:
    """Gracefully stop the running daemon."""
    cfg = load_config()
    ok = asyncio.run(stop_daemon(cfg.daemon.socket_path))
    if ok:
        console.print("Daemon stopped")
        return
    err_console.print("[yellow]No daemon was running[/yellow]")


@daemon_app.command("restart")
def cmd_daemon_restart() -> None:
    """Stop the running daemon (if any) and start a fresh one."""
    cfg = load_config()

    async def _go() -> bool:
        await stop_daemon(cfg.daemon.socket_path)
        return await ensure_running(
            cfg.daemon.socket_path,
            auto_spawn=True,
            log_path=cfg.daemon.log_path,
        )

    if asyncio.run(_go()):
        console.print(f"Daemon restarted at {cfg.daemon.socket_path}")
        return
    err_console.print("[red]Daemon failed to restart[/red]")
    raise typer.Exit(code=exits.ACCESSORY_UNREACHABLE)


@daemon_app.command("logs")
def cmd_daemon_logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Tail this many lines"),
) -> None:
    """Show the tail of the daemon log file."""
    cfg = load_config()
    log_path = Path(cfg.daemon.log_path)
    if not log_path.exists():
        err_console.print(f"[yellow]No log file at {log_path}[/yellow]")
        raise typer.Exit(code=1)
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        tail = fh.readlines()[-lines:]
    console.print("".join(tail), end="")


def main() -> None:  # entry point alternative for `python -m homekit.cli.main`
    app()


if __name__ == "__main__":
    main()
