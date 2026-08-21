"""Local IPC layer for fast command dispatch.

POSIX hosts use a Unix domain socket. Windows uses a loopback-only TCP
endpoint because asyncio has no portable named-pipe server API.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_SOCKET_PATH = "/tmp/lampgo.sock"
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 28420
IPC_TOKEN_FIELD = "_lampgo_ipc_token"
IPC_TOKEN_PATH_ENV = "LAMPGO_IPC_TOKEN_FILE"
TCP_PORT_BASE = 20000
TCP_PORT_SPAN = 20000
DARWIN_SUN_PATH_MAX = 103
LINUX_SUN_PATH_MAX = 107


@dataclass(frozen=True)
class _Endpoint:
    kind: str
    address: str | tuple[str, int]


def _get_socket_path() -> str:
    return os.environ.get("LAMPGO_SOCKET", DEFAULT_SOCKET_PATH)


def _get_token_path(token_path: str | Path | None = None) -> Path:
    if token_path is not None:
        return Path(token_path).expanduser()
    override = os.environ.get(IPC_TOKEN_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".lampgo" / "ipc-token"


def _chmod_private(path: Path, mode: int, event: str) -> None:
    try:
        os.chmod(path, mode)
    except OSError as exc:
        logger.warning(event, path=str(path), error=str(exc))


def _read_ipc_token(token_path: str | Path | None = None) -> str:
    path = _get_token_path(token_path)
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError(f"LampGo IPC token file is invalid: {path}")
    return token


def _ensure_ipc_token(token_path: str | Path | None = None) -> str:
    """Create or load the per-user token required by loopback TCP IPC."""
    path = _get_token_path(token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod_private(path.parent, 0o700, "ipc.token_directory_chmod_failed")

    try:
        token = _read_ipc_token(path)
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            token = _read_ipc_token(path)
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as token_file:
                token_file.write(token + "\n")
    _chmod_private(path, 0o600, "ipc.token_chmod_failed")
    return token


def _max_unix_socket_path_len() -> int:
    """Return a conservative AF_UNIX path length per platform."""
    if os.name != "posix":
        return LINUX_SUN_PATH_MAX
    if hasattr(os, "uname") and os.uname().sysname == "Darwin":
        return DARWIN_SUN_PATH_MAX
    return LINUX_SUN_PATH_MAX


def _parse_tcp_uri(path: str) -> tuple[str, int] | None:
    """Parse an explicit tcp://host:port endpoint."""
    raw = str(path or "").strip()
    if not raw.lower().startswith("tcp://"):
        return None
    parsed = urlsplit(raw)
    host = parsed.hostname or DEFAULT_TCP_HOST
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("LampGo IPC TCP endpoints must bind to the loopback address")
    if parsed.port is None or not 0 <= parsed.port <= 65535:
        raise ValueError("LampGo IPC TCP port must be between 0 and 65535")
    return host, parsed.port


def _tcp_port_for_path(path: str) -> int:
    """Return a stable local TCP port for a Windows socket-path setting."""
    explicit = _parse_tcp_uri(path)
    if explicit is not None:
        return explicit[1]

    override = os.environ.get("LAMPGO_IPC_PORT", "").strip()
    if override:
        try:
            port = int(override)
        except ValueError as exc:
            raise ValueError("LAMPGO_IPC_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("LAMPGO_IPC_PORT must be between 1 and 65535")
        return port

    if path == DEFAULT_SOCKET_PATH:
        return DEFAULT_TCP_PORT
    digest = hashlib.sha1(path.encode("utf-8")).digest()
    return TCP_PORT_BASE + int.from_bytes(digest[:4], "big") % TCP_PORT_SPAN


def _endpoint_for_path(path: str) -> _Endpoint:
    """Resolve a configured path to the actual local transport."""
    explicit = _parse_tcp_uri(path)
    if explicit is not None:
        return _Endpoint("tcp", explicit)
    if os.name == "nt":
        return _Endpoint("tcp", (DEFAULT_TCP_HOST, _tcp_port_for_path(path)))
    return _Endpoint("unix", _normalize_unix_socket_path(path))


def _normalize_unix_socket_path(path: str) -> str:
    """Map long Unix socket paths to a deterministic short /tmp path."""
    if len(path.encode("utf-8")) <= _max_unix_socket_path_len():
        return path
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
    fallback = f"/tmp/lampgo-{digest}.sock"
    logger.warning("ipc.socket_path_too_long", original=path, fallback=fallback)
    return fallback


def _normalize_socket_path(path: str) -> str:
    """Return the actual endpoint identifier used by server and client."""
    endpoint = _endpoint_for_path(str(path or _get_socket_path()))
    if endpoint.kind == "tcp":
        host, port = endpoint.address
        return _tcp_uri(host, port)
    return str(endpoint.address)


def _tcp_uri(host: str, port: int) -> str:
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"tcp://{display_host}:{port}"


def _is_socket_file(path: Path) -> bool:
    try:
        return stat.S_ISSOCK(path.stat().st_mode)
    except OSError:
        return False


class IPCServer:
    """Asyncio local IPC server."""

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        socket_path: str | None = None,
        token_path: str | Path | None = None,
    ) -> None:
        self._handler = handler
        raw_socket_path = socket_path or _get_socket_path()
        self._socket_path = _normalize_socket_path(raw_socket_path)
        self._endpoint = _endpoint_for_path(self._socket_path)
        self._token_path = _get_token_path(token_path)
        self._token: str | None = None
        self._server: asyncio.AbstractServer | None = None

    @property
    def socket_path(self) -> str:
        """Actual endpoint identifier after normalization."""
        return self._socket_path

    async def start(self) -> None:
        if self._endpoint.kind == "tcp":
            host, port = self._endpoint.address
            self._token = _ensure_ipc_token(self._token_path)
            self._server = await asyncio.start_server(self._handle_connection, host=host, port=port)
            sockets = self._server.sockets or []
            if sockets:
                actual_port = int(sockets[0].getsockname()[1])
                self._endpoint = _Endpoint("tcp", (host, actual_port))
                self._socket_path = _tcp_uri(host, actual_port)
            logger.info("ipc.started", endpoint=self._socket_path, transport="tcp")
            return

        path = Path(self._endpoint.address)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if not _is_socket_file(path):
                raise RuntimeError(f"IPC path exists and is not a socket: {path}")
            path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_connection, path=str(path))
        try:
            os.chmod(str(path), 0o660)
        except OSError as exc:
            logger.warning("ipc.socket_chmod_failed", path=str(path), error=str(exc))
        logger.info("ipc.started", endpoint=self._socket_path, transport="unix")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._endpoint.kind == "unix":
            path = Path(self._endpoint.address)
            if path.exists() and _is_socket_file(path):
                path.unlink()
        logger.info("ipc.stopped")

    async def _write_json(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> bool:
        """Write one JSON line; ignore disconnects from short-lived clients."""
        try:
            writer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
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
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                await self._write_json(writer, {"ok": False, "error": "invalid request"})
                return
            if self._endpoint.kind == "tcp":
                supplied_token = request.pop(IPC_TOKEN_FIELD, None)
                token_matches = False
                if isinstance(supplied_token, str) and self._token is not None:
                    try:
                        token_matches = hmac.compare_digest(
                            supplied_token.encode("ascii"),
                            self._token.encode("ascii"),
                        )
                    except UnicodeEncodeError:
                        token_matches = False
                if not token_matches:
                    logger.warning("ipc.unauthorized_client", peer=writer.get_extra_info("peername"))
                    await self._write_json(writer, {"ok": False, "error": "unauthorized"})
                    return
            response = await self._handler(request)
            await self._write_json(writer, response)
        except TimeoutError:
            await self._write_json(writer, {"ok": False, "error": "timeout"})
        except json.JSONDecodeError as exc:
            await self._write_json(writer, {"ok": False, "error": f"invalid json: {exc}"})
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


def ipc_send(
    request: dict[str, Any],
    socket_path: str | None = None,
    timeout: float = 30.0,
    token_path: str | Path | None = None,
) -> dict[str, Any]:
    """Send one IPC request.

    Raises connection/socket errors when the endpoint is unavailable, plus
    ``FileNotFoundError`` or ``ValueError`` when a TCP token is missing or invalid.
    """
    raw_path = socket_path or _get_socket_path()
    endpoint = _endpoint_for_path(raw_path)
    payload = dict(request)
    if endpoint.kind == "tcp":
        payload[IPC_TOKEN_FIELD] = _read_ipc_token(token_path)
        sock = socket.create_connection(endpoint.address, timeout=timeout)
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(endpoint.address)
    try:
        sock.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        buf = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        return json.loads(buf.strip().decode("utf-8"))
    finally:
        sock.close()


def is_daemon_running(socket_path: str | None = None) -> bool:
    """Check whether a daemon is listening on the configured endpoint."""
    raw_path = socket_path or _get_socket_path()
    endpoint = _endpoint_for_path(raw_path)
    if endpoint.kind == "unix" and not Path(endpoint.address).exists():
        return False
    try:
        result = ipc_send({"cmd": "ping"}, socket_path=raw_path, timeout=2.0)
        return bool(result.get("ok", False))
    except (ConnectionRefusedError, FileNotFoundError, OSError, TimeoutError, ValueError):
        return False


def cleanup_ipc_endpoint(socket_path: str | None = None) -> bool:
    """Remove a stale Unix socket; TCP endpoints need no filesystem cleanup."""
    raw_path = socket_path or _get_socket_path()
    endpoint = _endpoint_for_path(raw_path)
    if endpoint.kind != "unix":
        return False
    path = Path(endpoint.address)
    if path.exists() and _is_socket_file(path):
        path.unlink()
        return True
    return False
