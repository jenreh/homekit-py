"""Daemon lifecycle helpers: status check, auto-spawn, graceful stop."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from homekit.daemon.client import DaemonRpcClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DaemonStatus:
    running: bool
    socket_path: Path
    pid: int | None
    detail: str = ""


def _read_pid(pid_path: Path) -> int | None:
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _socket_accepts(socket_path: Path) -> bool:
    if not socket_path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            sock.connect(str(socket_path))
            return True
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError):
        return False


def status(socket_path: Path | str, pid_path: Path | str | None = None) -> DaemonStatus:
    """Return whether a daemon is reachable and (if known) its PID."""
    sp = Path(socket_path)
    pp = Path(pid_path) if pid_path else None
    pid = _read_pid(pp) if pp is not None else None
    if _socket_accepts(sp):
        return DaemonStatus(True, sp, pid, "ok")
    if pid is not None and _pid_alive(pid):
        return DaemonStatus(False, sp, pid, "pid alive but socket unreachable")
    return DaemonStatus(False, sp, pid, "not running")


def spawn_detached(socket_path: Path | str, log_path: Path | str | None = None) -> int:
    """Spawn ``homekit-daemon`` as a detached background process. Returns its PID."""
    cmd = [sys.executable, "-m", "homekit.daemon.main", "--socket-path", str(socket_path)]
    if log_path is not None:
        cmd += ["--log-path", str(log_path)]
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return proc.pid


async def wait_for_socket(socket_path: Path | str, timeout_s: float = 5.0) -> bool:
    sp = Path(socket_path)
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if _socket_accepts(sp):
            return True
        await asyncio.sleep(0.05)
    return False


async def ensure_running(
    socket_path: Path | str,
    *,
    auto_spawn: bool = True,
    log_path: Path | str | None = None,
    timeout_s: float = 10.0,
) -> bool:
    """Connect to daemon, spawning one if it isn't already reachable.

    Returns True when the socket is reachable after the call.
    """
    sp = Path(socket_path)
    if _socket_accepts(sp):
        return True
    if not auto_spawn:
        return False
    pid = spawn_detached(sp, log_path=log_path)
    logger.debug("Spawned daemon pid=%d for socket=%s", pid, sp)
    return await wait_for_socket(sp, timeout_s=timeout_s)


async def stop_daemon(socket_path: Path | str, timeout_s: float = 5.0) -> bool:
    """Send a graceful shutdown RPC. Returns True if the daemon acknowledged."""
    sp = Path(socket_path)
    if not _socket_accepts(sp):
        return False
    rpc = DaemonRpcClient(sp)
    try:
        await rpc.connect()
        try:
            await asyncio.wait_for(rpc.call("shutdown"), timeout=timeout_s)
        finally:
            await rpc.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("shutdown RPC failed: %s", exc)
        return False
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if not _socket_accepts(sp):
            return True
        await asyncio.sleep(0.05)
    return not _socket_accepts(sp)
