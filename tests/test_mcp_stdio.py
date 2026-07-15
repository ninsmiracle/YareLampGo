import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from lampgo import mcp_stdio


@pytest.mark.asyncio
async def test_mcp_initialize_and_tools_list() -> None:
    initialized = await mcp_stdio._handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
    )
    listed = await mcp_stdio._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

    assert initialized["result"]["serverInfo"]["name"] == "lampgo"
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {"lampgo_status", "lampgo_invoke", "lampgo_ask_user"} <= names
    status = next(tool for tool in listed["result"]["tools"] if tool["name"] == "lampgo_status")
    assert status["annotations"]["readOnlyHint"] is True


@pytest.mark.asyncio
async def test_mcp_tool_call_proxies_with_structured_result(monkeypatch) -> None:
    request_kwargs = {}

    async def fake_request(method, path, payload=None, **kwargs):
        request_kwargs.update(kwargs)
        return {"ok": True, "result": {"method": method, "path": path, "payload": payload}}

    monkeypatch.setattr(mcp_stdio, "_daemon_request", fake_request)
    response = await mcp_stdio._handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "lampgo_invoke", "arguments": {"skill_id": "nod", "params": {"count": 1}}},
        }
    )

    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["result"]["path"] == "/api/invoke"
    assert payload["result"]["payload"]["skill_id"] == "nod"
    assert request_kwargs["timeout_s"] == mcp_stdio._SKILL_INVOKE_TIMEOUT_S


@pytest.mark.asyncio
async def test_cancelling_inflight_invoke_stops_daemon_skill(monkeypatch) -> None:
    requests = []

    async def fake_request(method, path, payload=None, **kwargs):
        requests.append((method, path, payload, kwargs))
        return {"ok": True}

    monkeypatch.setattr(mcp_stdio, "_daemon_request", fake_request)
    await mcp_stdio._cancel_inflight_tool(
        {
            "method": "tools/call",
            "params": {"name": "lampgo_invoke", "arguments": {"skill_id": "nod"}},
        }
    )

    assert requests == [("POST", "/api/cancel", None, {"timeout_s": 10.0})]


@pytest.mark.asyncio
async def test_mcp_stdio_handles_ping_while_cancelling_long_request(monkeypatch) -> None:
    tool_started = threading.Event()
    ping_written = threading.Event()
    cancellation_written = threading.Event()
    output_chunks: list[bytes] = []

    async def blocking_tool(_name, _args):
        tool_started.set()
        await asyncio.Event().wait()

    class InputBuffer:
        def __init__(self) -> None:
            self.index = 0
            self.lines = [
                b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lampgo_status","arguments":{}}}\n',
                b'{"jsonrpc":"2.0","id":2,"method":"ping","params":{}}\n',
                b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":1}}\n',
            ]

        def readline(self) -> bytes:
            if self.index == 2:
                assert tool_started.wait(timeout=2.0)
            if self.index >= len(self.lines):
                assert ping_written.wait(timeout=2.0)
                assert cancellation_written.wait(timeout=2.0)
                return b""
            line = self.lines[self.index]
            self.index += 1
            return line

    class OutputBuffer:
        def write(self, data: bytes) -> int:
            output_chunks.append(data)
            if b'"id":2' in data:
                ping_written.set()
            if b'"id":1' in data and b'"code":-32800' in data:
                cancellation_written.set()
            return len(data)

        def flush(self) -> None:
            return None

    monkeypatch.setattr(mcp_stdio, "_call_tool", blocking_tool)
    monkeypatch.setattr(mcp_stdio.sys, "stdin", SimpleNamespace(buffer=InputBuffer()))
    monkeypatch.setattr(mcp_stdio.sys, "stdout", SimpleNamespace(buffer=OutputBuffer()))

    await mcp_stdio.run_mcp_stdio()

    responses = [json.loads(line) for chunk in output_chunks for line in chunk.splitlines()]
    by_id = {response["id"]: response for response in responses}
    assert by_id[2]["result"] == {}
    assert by_id[1]["error"]["code"] == -32800
