"""Thin wrapper to invoke OpenClaw via its CLI.

For demo-grade integration, the most stable way to enter the real OpenClaw
execution chain is to shell out to `openclaw agent ...` on the same host.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenClawRunResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int


async def run_openclaw_agent(message: str, *, thinking: str = "high", env: dict[str, str] | None = None) -> OpenClawRunResult:
    if shutil.which("openclaw") is None:
        return OpenClawRunResult(ok=False, stdout="", stderr="openclaw binary not found on PATH", exit_code=127)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = await asyncio.create_subprocess_exec(
        "openclaw",
        "agent",
        "--agent",
        "main",
        "--message",
        message,
        "--thinking",
        thinking,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
    )
    out_b, err_b = await proc.communicate()
    stdout = out_b.decode(errors="replace")
    stderr = err_b.decode(errors="replace")
    code = int(proc.returncode or 0)
    return OpenClawRunResult(ok=code == 0, stdout=stdout, stderr=stderr, exit_code=code)

