"""Tests for the IPC server and client."""

from __future__ import annotations

import asyncio
import json
import socket as socket_mod

import pytest

from lampgo.ipc import IPCServer, ipc_send, is_daemon_running


async def echo_handler(request: dict) -> dict:
    return {"ok": True, "result": request}


@pytest.mark.asyncio
async def test_ipc_roundtrip(tmp_path):
    """Send a request and verify the echo response."""
    sock_path = str(tmp_path / "test.sock")
    server = IPCServer(echo_handler, socket_path=sock_path)
    await server.start()
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: ipc_send({"cmd": "ping", "data": 42}, socket_path=sock_path)
        )
        assert result["ok"] is True
        assert result["result"]["cmd"] == "ping"
        assert result["result"]["data"] == 42
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_ipc_invoke_request(tmp_path):
    sock_path = str(tmp_path / "test.sock")
    server = IPCServer(echo_handler, socket_path=sock_path)
    await server.start()
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: ipc_send(
                {"cmd": "invoke", "skill_id": "nod", "params": {"count": 2}},
                socket_path=sock_path,
            ),
        )
        assert result["ok"] is True
        assert result["result"]["skill_id"] == "nod"
    finally:
        await server.stop()


def test_is_daemon_running_false():
    """Should return False when no daemon is running."""
    assert is_daemon_running("/tmp/nonexistent_lampgo_test.sock") is False


@pytest.mark.asyncio
async def test_ipc_server_handles_invalid_json(tmp_path):
    """Server should handle malformed JSON gracefully."""
    sock_path = str(tmp_path / "test.sock")
    server = IPCServer(echo_handler, socket_path=sock_path)
    await server.start()
    try:

        def _send_bad():
            sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(sock_path)
            sock.sendall(b"not json\n")
            buf = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break
            sock.close()
            return json.loads(buf.strip())

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _send_bad)
        assert result["ok"] is False
        assert "invalid json" in result["error"]
    finally:
        await server.stop()
