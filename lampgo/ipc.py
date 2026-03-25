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
import json
import os
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_SOCKET_PATH = "/tmp/lampgo.sock"


def _get_socket_path() -> str:
    return os.environ.get("LAMPGO_SOCKET", DEFAULT_SOCKET_PATH)


class IPCServer:
    """Asyncio Unix socket server that dispatches JSON commands."""

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        socket_path: str | None = None,
    ) -> None:
        self._handler = handler
        self._socket_path = socket_path or _get_socket_path()
        self._server: asyncio.AbstractServer | None = None

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

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not raw:
                return
            request = json.loads(raw.decode())
            response = await self._handler(request)
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()
        except TimeoutError:
            writer.write(json.dumps({"ok": False, "error": "timeout"}).encode() + b"\n")
            await writer.drain()
        except json.JSONDecodeError as e:
            writer.write(json.dumps({"ok": False, "error": f"invalid json: {e}"}).encode() + b"\n")
            await writer.drain()
        except Exception:
            logger.exception("ipc.handler_error")
            try:
                writer.write(json.dumps({"ok": False, "error": "internal error"}).encode() + b"\n")
                await writer.drain()
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
    path = socket_path or _get_socket_path()
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
    path = socket_path or _get_socket_path()
    if not Path(path).exists():
        return False
    try:
        result = ipc_send({"cmd": "ping"}, socket_path=path, timeout=2.0)
        return result.get("ok", False)
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False
