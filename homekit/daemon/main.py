"""Entry point for the ``homekit-daemon`` console script."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path

from homekit.client import HomeKitClient
from homekit.config import load_config
from homekit.daemon.server import DaemonServer

logger = logging.getLogger(__name__)


def _setup_logging(log_path: Path | None, verbose: bool) -> None:
    handlers: list[logging.Handler] = []
    level = logging.DEBUG if verbose else logging.INFO
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_path, maxBytes=1 << 20, backupCount=3))
    handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def _write_pid(pid_path: Path) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid(pid_path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        pid_path.unlink()


async def _serve(server: DaemonServer) -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop(*_: object) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)

    serve_task = asyncio.create_task(server.serve_forever())
    stop_task = asyncio.create_task(stop_event.wait())
    _done, pending = await asyncio.wait(
        {serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in pending:
        with contextlib.suppress(BaseException):
            await task
    await server.stop()


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="homekit-daemon", description="homekit-py daemon")
    parser.add_argument("--socket-path", type=str, default="")
    parser.add_argument("--pid-path", type=str, default="")
    parser.add_argument("--log-path", type=str, default="")
    parser.add_argument("--idle-timeout-s", type=float, default=-1.0)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    config = load_config()
    daemon_cfg = config.daemon
    socket_path = Path(args.socket_path or daemon_cfg.socket_path)
    pid_path = Path(args.pid_path or daemon_cfg.pid_path)
    log_path = Path(args.log_path or daemon_cfg.log_path)
    idle_timeout = (
        args.idle_timeout_s if args.idle_timeout_s >= 0 else daemon_cfg.idle_timeout_s
    )

    _setup_logging(log_path, args.verbose)
    _write_pid(pid_path)
    logger.info("Starting daemon, socket=%s pid=%d", socket_path, os.getpid())
    client = HomeKitClient(config=config)
    server = DaemonServer(client, socket_path, idle_timeout_s=idle_timeout)

    try:
        asyncio.run(_run(server))
    finally:
        _remove_pid(pid_path)
    return 0


async def _run(server: DaemonServer) -> None:
    await server.start()
    await _serve(server)


if __name__ == "__main__":
    raise SystemExit(run())
