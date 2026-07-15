"""Codex discovery, zero-config registration, and JSONL task execution."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_APP_CANDIDATES = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
    Path("/Applications/Codex.app/Contents/Resources/codex"),
)
_ALLOWED_SANDBOXES = frozenset({"read-only", "workspace-write"})
_PROCESS_STOP_TIMEOUT_S = 3.0


@dataclass
class CodexStatus:
    connection: str
    binary_path: str = ""
    version: str = ""
    logged_in: bool = False
    mcp_registered: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CodexRunResult:
    ok: bool
    exit_code: int
    final_message: str = ""
    thread_id: str = ""
    stderr: str = ""


def find_codex_binary() -> Path | None:
    override = str(os.environ.get("CODEX_CLI_PATH") or "").strip()
    candidates = [Path(override).expanduser()] if override else []
    found = shutil.which("codex")
    if found:
        candidates.append(Path(found))
    candidates.extend(_APP_CANDIDATES)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _run_quiet(command: list[str], *, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def _mcp_command() -> list[str]:
    return [sys.executable, "-m", "lampgo.cli", "mcp-stdio"]


def _lampgo_entry(entries: Any) -> dict[str, Any] | None:
    if not isinstance(entries, list):
        return None
    return next((item for item in entries if isinstance(item, dict) and item.get("name") == "lampgo"), None)


def _entry_matches(entry: dict[str, Any] | None) -> bool:
    if not entry or entry.get("enabled") is False:
        return False
    transport = entry.get("transport")
    if not isinstance(transport, dict) or transport.get("type") != "stdio":
        return False
    command, *args = _mcp_command()
    return transport.get("command") == command and list(transport.get("args") or []) == args


def detect_codex_integration() -> CodexStatus:
    binary = find_codex_binary()
    if binary is None:
        return CodexStatus(connection="not_installed", detail="未检测到 Codex")
    try:
        version_proc = _run_quiet([str(binary), "--version"])
        login_proc = _run_quiet([str(binary), "login", "status"])
        list_proc = _run_quiet([str(binary), "mcp", "list", "--json"])
        entries = json.loads(list_proc.stdout or "[]") if list_proc.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return CodexStatus(connection="error", binary_path=str(binary), detail=str(exc))
    logged_in = login_proc.returncode == 0
    entry = _lampgo_entry(entries)
    registered = _entry_matches(entry)
    if not logged_in:
        connection = "login_required"
        detail = "Codex 尚未登录"
    elif registered:
        connection = "connected"
        detail = "Codex 已接通"
    else:
        connection = "provisioning"
        detail = "正在修复 LampGo 工具注册" if entry else "正在注册 LampGo 工具"
    return CodexStatus(
        connection=connection,
        binary_path=str(binary),
        version=(version_proc.stdout or version_proc.stderr).strip(),
        logged_in=logged_in,
        mcp_registered=registered,
        detail=detail,
    )


def ensure_codex_integration() -> CodexStatus:
    status = detect_codex_integration()
    if status.connection in {"not_installed", "login_required", "error"} or status.mcp_registered:
        return status
    binary = Path(status.binary_path)
    list_proc = _run_quiet([str(binary), "mcp", "list", "--json"])
    try:
        existing = _lampgo_entry(json.loads(list_proc.stdout or "[]"))
    except json.JSONDecodeError:
        existing = None
    if existing is not None:
        remove_proc = _run_quiet([str(binary), "mcp", "remove", "lampgo"])
        if remove_proc.returncode != 0:
            status.connection = "error"
            status.detail = (remove_proc.stderr or remove_proc.stdout or "Codex MCP 旧配置清理失败").strip()
            return status
    command = _mcp_command()
    proc = _run_quiet([str(binary), "mcp", "add", "lampgo", "--", *command], timeout=15.0)
    if proc.returncode != 0:
        status.connection = "error"
        status.detail = (proc.stderr or proc.stdout or "Codex MCP 注册失败").strip()
        return status
    return detect_codex_integration()


class CodexProvider:
    name = "codex"

    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def run(
        self,
        *,
        task_id: str,
        prompt: str,
        workspace: Path,
        sandbox: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> CodexRunResult:
        if sandbox not in _ALLOWED_SANDBOXES:
            return CodexRunResult(
                ok=False,
                exit_code=2,
                stderr=f"不支持的 Codex sandbox：{sandbox}",
            )
        status = await asyncio.to_thread(ensure_codex_integration)
        if status.connection != "connected":
            return CodexRunResult(ok=False, exit_code=127, stderr=status.detail)

        command = [
            status.binary_path,
            "-a",
            "never",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            sandbox,
            "-C",
            str(workspace),
            "-",
        ]
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._processes[task_id] = proc
        thread_id = ""
        final_message = ""
        stderr_chunks: list[str] = []

        async def read_stderr() -> None:
            assert proc.stderr is not None
            while line := await proc.stderr.readline():
                stderr_chunks.append(line.decode("utf-8", errors="replace"))

        stderr_task = asyncio.create_task(read_stderr())
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            assert proc.stdout is not None
            while line := await proc.stdout.readline():
                raw = line.decode("utf-8", errors="replace").strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    event = {"type": "output", "text": raw}
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or "event")
                if event_type == "thread.started":
                    thread_id = str(event.get("thread_id") or "")
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    final_message = str(item.get("text") or final_message)
                await on_event(event)
            code = await proc.wait()
            await stderr_task
        finally:
            self._processes.pop(task_id, None)
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_PROCESS_STOP_TIMEOUT_S)
                except TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        return CodexRunResult(
            ok=code == 0,
            exit_code=code,
            final_message=final_message.strip(),
            thread_id=thread_id,
            stderr="".join(stderr_chunks).strip(),
        )

    async def cancel(self, task_id: str) -> bool:
        proc = self._processes.get(task_id)
        if proc is None or proc.returncode is not None:
            return False
        try:
            proc.terminate()
        except ProcessLookupError:
            return False
        try:
            await asyncio.wait_for(proc.wait(), timeout=_PROCESS_STOP_TIMEOUT_S)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        return True
