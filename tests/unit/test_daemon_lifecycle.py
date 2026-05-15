"""Lifecycle helpers — status detection, graceful stop, ensure_running."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

from homekit.client import HomeKitClient
from homekit.config import load_config
from homekit.daemon.lifecycle import ensure_running, status, stop_daemon
from homekit.daemon.server import DaemonServer
from tests.fake_backend import FakeBackend


def _short_socket() -> Path:
    return Path(tempfile.gettempdir()) / f"hk-{uuid.uuid4().hex[:8]}.sock"


async def test_status_reports_not_running_when_socket_missing(tmp_path: Path) -> None:
    st = status(tmp_path / "missing.sock")
    assert st.running is False
    assert "not running" in st.detail


async def test_status_running_after_server_start(tmp_homekit_config) -> None:
    backend = FakeBackend()
    client = HomeKitClient(config=load_config(), backend=backend)
    socket_path = _short_socket()
    server = DaemonServer(client, socket_path, idle_timeout_s=300.0)
    await server.start()
    try:
        st = status(socket_path)
        assert st.running is True
    finally:
        await server.stop()
        if socket_path.exists():
            socket_path.unlink()


async def test_stop_daemon_returns_false_when_no_socket(tmp_path: Path) -> None:
    assert await stop_daemon(tmp_path / "missing.sock") is False


async def test_stop_daemon_shuts_down_running_server(tmp_homekit_config) -> None:
    backend = FakeBackend()
    client = HomeKitClient(config=load_config(), backend=backend)
    socket_path = _short_socket()
    server = DaemonServer(client, socket_path, idle_timeout_s=300.0)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())
    try:
        assert await stop_daemon(socket_path) is True
        await asyncio.wait_for(serve_task, timeout=2.0)
    finally:
        await server.stop()
        if socket_path.exists():
            socket_path.unlink()


async def test_ensure_running_returns_false_when_auto_spawn_off(tmp_path: Path) -> None:
    ok = await ensure_running(
        tmp_path / "missing.sock", auto_spawn=False, timeout_s=0.2
    )
    assert ok is False
