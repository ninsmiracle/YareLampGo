"""Minimal stdio MCP proxy from Codex to the running LampGo daemon.

The transport is deliberately dependency-free: MCP stdio messages are JSON-RPC
objects separated by newlines.  All robot work still goes through the daemon's
authenticated HTTP API and therefore through its normal safety boundaries.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from lampgo import personastore

_DEFAULT_DAEMON_TIMEOUT_S = 135.0
_SKILL_INVOKE_TIMEOUT_S = 310.0
_REQUEST_CANCELLED_CODE = -32800

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "lampgo_status",
        "description": "Read the current LampGo robot, motion, and safety status.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "lampgo_list_skills",
        "description": "List the LampGo skills that can be invoked safely.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "lampgo_invoke",
        "description": "Invoke one registered LampGo skill. Use lampgo_list_skills first when unsure.",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "Exact registered LampGo skill id"},
                "params": {"type": "object", "description": "Skill parameters", "additionalProperties": True},
                "wait": {"type": "boolean", "default": True},
            },
            "required": ["skill_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lampgo_camera_snap",
        "description": "Capture the latest image from LampGo's lamp-head camera.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "lampgo_ask_user",
        "description": "Ask the user a question through LampGo voice and Web UI, then wait for the reply.",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "timeout_s": {"type": "number", "minimum": 5, "maximum": 600, "default": 120},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lampgo_agent_tasks",
        "description": "List complex tasks currently managed by LampGo.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def _runtime_api_base() -> str:
    override = str(os.environ.get("LAMPGO_API_BASE") or "").strip()
    if override:
        return override.rstrip("/")
    home = Path(os.environ.get("LAMPGO_HOME") or Path.home() / ".lampgo")
    try:
        runtime = json.loads((home / "runtime.json").read_text(encoding="utf-8"))
        api_base = str(runtime.get("api_base") or "").strip()
        if api_base:
            return api_base.rstrip("/")
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    overrides = personastore.get_overrides_toml() or {}
    web = overrides.get("web") if isinstance(overrides.get("web"), dict) else {}
    port = int(web.get("port") or 8420)
    return f"http://127.0.0.1:{port}"


async def _daemon_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_s: float = _DEFAULT_DAEMON_TIMEOUT_S,
) -> dict[str, Any]:
    token = personastore.get_or_create_local_api_token()
    headers = {"authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(
            base_url=_runtime_api_base(),
            headers=headers,
            timeout=timeout_s,
            trust_env=False,
        ) as client:
            response = await client.request(method, path, json=payload)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"LampGo daemon unavailable: {exc}"}
    try:
        body = response.json()
    except ValueError:
        body = {"ok": False, "error": response.text or f"HTTP {response.status_code}"}
    if response.status_code >= 400 and isinstance(body, dict):
        body.setdefault("ok", False)
        body.setdefault("error", f"HTTP {response.status_code}")
    return body if isinstance(body, dict) else {"ok": False, "error": "invalid daemon response"}


async def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "lampgo_status":
        return await _daemon_request("GET", "/api/status")
    if name == "lampgo_list_skills":
        return await _daemon_request("GET", "/api/skills")
    if name == "lampgo_invoke":
        skill_id = str(args.get("skill_id") or "").strip()
        if not skill_id:
            return {"ok": False, "error": "skill_id is required"}
        params = args.get("params") if isinstance(args.get("params"), dict) else {}
        wait = bool(args.get("wait", True))
        return await _daemon_request(
            "POST",
            "/api/invoke",
            {"skill_id": skill_id, "params": params, "wait": wait},
            timeout_s=_SKILL_INVOKE_TIMEOUT_S if wait else _DEFAULT_DAEMON_TIMEOUT_S,
        )
    if name == "lampgo_camera_snap":
        return await _daemon_request("GET", "/api/camera/snap")
    if name == "lampgo_ask_user":
        question = str(args.get("question") or "").strip()
        if not question:
            return {"ok": False, "error": "question is required"}
        timeout_s = max(5.0, min(600.0, float(args.get("timeout_s") or 120)))
        return await _daemon_request(
            "POST",
            "/api/agent/ask",
            {
                "question": question,
                "options": list(args.get("options") or []),
                "timeout_s": timeout_s,
            },
            timeout_s=timeout_s + 10.0,
        )
    if name == "lampgo_agent_tasks":
        return await _daemon_request("GET", "/api/agent/tasks")
    return {"ok": False, "error": f"unknown tool: {name}"}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = str(message.get("method") or "")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if request_id is None:
        return None
    if method == "initialize":
        protocol = str(params.get("protocolVersion") or "2024-11-05")
        return _result(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "lampgo", "version": "0.1.0"},
                "instructions": "Use these tools to observe or safely control the local LampGo desk lamp.",
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": _TOOLS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        payload = await _call_tool(name, arguments)
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return _result(
            request_id,
            {"content": [{"type": "text", "text": text}], "isError": payload.get("ok") is False},
        )
    if method == "resources/list":
        return _result(request_id, {"resources": []})
    if method == "prompts/list":
        return _result(request_id, {"prompts": []})
    return _error(request_id, -32601, f"method not found: {method}")


async def _cancel_inflight_tool(message: dict[str, Any]) -> None:
    if message.get("method") != "tools/call":
        return
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if params.get("name") == "lampgo_invoke":
        await _daemon_request("POST", "/api/cancel", timeout_s=10.0)


async def run_mcp_stdio() -> None:
    """Serve concurrent MCP requests until Codex closes stdin."""
    pending: dict[str | int, asyncio.Task[None]] = {}
    write_lock = asyncio.Lock()

    async def write_response(response: dict[str, Any]) -> None:
        data = json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with write_lock:
            sys.stdout.buffer.write(data.encode("utf-8"))
            sys.stdout.buffer.flush()

    async def run_request(message: dict[str, Any], request_id: str | int) -> None:
        try:
            try:
                response = await _handle(message)
            except asyncio.CancelledError:
                try:
                    await _cancel_inflight_tool(message)
                except Exception:
                    pass
                response = _error(request_id, _REQUEST_CANCELLED_CODE, "request cancelled")
            except Exception as exc:  # noqa: BLE001
                response = _error(request_id, -32603, str(exc))
            if response is not None:
                await write_response(response)
        finally:
            if pending.get(request_id) is asyncio.current_task():
                pending.pop(request_id, None)

    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not line:
                break
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("request must be an object")
            except (json.JSONDecodeError, ValueError) as exc:
                await write_response(_error(None, -32700, str(exc)))
                continue

            method = str(message.get("method") or "")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if method == "notifications/cancelled":
                cancelled_id = params.get("requestId")
                if isinstance(cancelled_id, (str, int)):
                    task = pending.get(cancelled_id)
                    if task is not None and not task.done():
                        task.cancel()
                continue

            request_id = message.get("id")
            if request_id is None:
                continue
            if not isinstance(request_id, (str, int)):
                await write_response(_error(None, -32600, "request id must be a string or integer"))
                continue
            if request_id in pending:
                await write_response(_error(request_id, -32600, "duplicate request id"))
                continue
            task = asyncio.create_task(run_request(message, request_id))
            pending[request_id] = task
            await asyncio.sleep(0)
    finally:
        tasks = list(pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(run_mcp_stdio())


if __name__ == "__main__":
    main()
