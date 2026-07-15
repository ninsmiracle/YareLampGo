import json

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
    async def fake_request(method, path, payload=None, **kwargs):
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
