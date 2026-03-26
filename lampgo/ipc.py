"""IPC layer — Unix socket server and client for fast command dispatch.

Protocol: JSON-over-Unix-socket, one request per connection.
Each client connects, sends a single JSON line, receives a JSON response, disconnects.

Request format:
    {"cmd": "invoke", "skill_id": "nod", "params": {"count": 3}}
    {"cmd": "status"}
    {"cmd": "skills"}
    {"cmd": "cancel"}
    {"cmd": "estop"}
    {"cmd": "text", "input": "做个害羞的表情"}

Response format:
    {"ok": true, "result": {...}}
    {"ok": false, "error": "..."}
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_SOCKET_PATH = "/tmp/lampgo.sock"
DARWIN_SUN_PATH_MAX = 103
LINUX_SUN_PATH_MAX = 107


def _get_socket_path() -> str:
    return os.environ.get("LAMPGO_SOCKET", DEFAULT_SOCKET_PATH)


def _max_unix_socket_path_len() -> int:
    """Return a conservative AF_UNIX path length per platform."""
    if os.name != "posix":
        return LINUX_SUN_PATH_MAX
    if hasattr(os, "uname") and os.uname().sysname == "Darwin":
        return DARWIN_SUN_PATH_MAX
    return LINUX_SUN_PATH_MAX


def _normalize_socket_path(path: str) -> str:
    """Map long Unix socket paths to a deterministic short /tmp path."""
    if len(path.encode()) <= _max_unix_socket_path_len():
        return path
    digest = hashlib.sha1(path.encode()).hexdigest()[:16]
    fallback = f"/tmp/lampgo-{digest}.sock"
    logger.warning("ipc.socket_path_too_long", original=path, fallback=fallback)
    return fallback


class IPCServer:
    """Asyncio Unix socket server that dispatches JSON commands."""

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        socket_path: str | None = None,
    ) -> None:
        self._handler = handler
        raw_socket_path = socket_path or _get_socket_path()
        self._socket_path = _normalize_socket_path(raw_socket_path)
        self._server: asyncio.AbstractServer | None = None

    @property
    def socket_path(self) -> str:
        """Actual socket path after normalization."""
        return self._socket_path

    async def start(self) -> None:
        path = Path(self._socket_path)
        if path.exists():
            path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_connection, path=str(path))
        os.chmod(str(path), 0o660)
        logger.info("ipc.started", socket=self._socket_path)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        path = Path(self._socket_path)
        if path.exists():
            path.unlink()
        logger.info("ipc.stopped")

    async def _write_json(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> bool:
        """Write one JSON line; ignore disconnects from short-lived clients."""
        try:
            writer.write(json.dumps(payload).encode() + b"\n")
            await writer.drain()
            return True
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("ipc.client_disconnected")
            return False

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not raw:
                return
            request = json.loads(raw.decode())
            response = await self._handler(request)
            await self._write_json(writer, response)
        except TimeoutError:
            await self._write_json(writer, {"ok": False, "error": "timeout"})
        except json.JSONDecodeError as e:
            await self._write_json(writer, {"ok": False, "error": f"invalid json: {e}"})
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("ipc.client_disconnected")
        except Exception:
            logger.exception("ipc.handler_error")
            try:
                await self._write_json(writer, {"ok": False, "error": "internal error"})
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


def ipc_send(request: dict[str, Any], socket_path: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
    """Synchronous IPC client. Sends one request and returns the response.

    Raises ConnectionRefusedError if daemon is not running.
    """
    raw_path = socket_path or _get_socket_path()
    path = _normalize_socket_path(raw_path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
        sock.sendall(json.dumps(request).encode() + b"\n")
        buf = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        return json.loads(buf.strip())
    finally:
        sock.close()


def is_daemon_running(socket_path: str | None = None) -> bool:
    """Check if a daemon is listening on the socket."""
    raw_path = socket_path or _get_socket_path()
    path = _normalize_socket_path(raw_path)
    if not Path(path).exists():
        return False
    try:
        result = ipc_send({"cmd": "ping"}, socket_path=path, timeout=2.0)
        return result.get("ok", False)
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False
