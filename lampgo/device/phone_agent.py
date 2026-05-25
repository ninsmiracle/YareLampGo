"""Bridge to Lampgo's built-in Open-AutoGLM phone agent process."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass

from lampgo.core.config import LLMConfig, PhoneAgentConfig


@dataclass
class PhoneTaskResult:
    ok: bool
    status: str
    duration_s: float
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""


class PhoneAgentRunner:
    """Run the vendored Open-AutoGLM agent in an isolated subprocess."""

    def __init__(self, phone_config: PhoneAgentConfig, llm_config: LLMConfig) -> None:
        self.config = phone_config
        self.llm_config = llm_config

    def _python_executable(self) -> str:
        configured = self.config.python_executable.strip()
        if configured:
            return configured

        return sys.executable or "python"

    def validate(self) -> str | None:
        if not self.config.enabled:
            return "phone agent is disabled; set LAMPGO_PHONE_ENABLED=true"
        if self.config.device_type.strip().lower() not in {"adb", "hdc", "ios"}:
            return "phone agent device_type must be one of: adb, hdc, ios"
        if not self.llm_config.api_base.strip():
            return "llm.api_base is empty; set LAMPGO_LLM_API_BASE for phone_task"
        if not self.llm_config.model.strip():
            return "llm.model is empty; set LAMPGO_LLM_MODEL for phone_task"
        return None

    async def run_task(
        self,
        task: str,
        *,
        max_steps: int | None = None,
        device_id: str | None = None,
        timeout_s: float | None = None,
        allow_sensitive: bool = False,
    ) -> PhoneTaskResult:
        validation_error = self.validate()
        if validation_error:
            return PhoneTaskResult(
                ok=False,
                status="not_configured",
                duration_s=0.0,
                error=validation_error,
            )

        resolved_steps = int(max_steps or self.config.default_max_steps)
        resolved_timeout = float(timeout_s or self.config.timeout_s)
        resolved_device_id = (device_id or self.config.device_id).strip()

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PHONE_AGENT_DEVICE_TYPE"] = self.config.device_type.strip().lower()
        env["PHONE_AGENT_BASE_URL"] = self.llm_config.api_base.strip()
        env["PHONE_AGENT_MODEL"] = self.llm_config.model.strip()
        env["PHONE_AGENT_API_KEY"] = self.llm_config.api_key.strip() or "EMPTY"
        env["PHONE_AGENT_MAX_STEPS"] = str(resolved_steps)
        env["PHONE_AGENT_SKIP_MODEL_CHECK"] = "true" if self.config.skip_model_check else "false"
        env["PHONE_AGENT_LANG"] = self.config.lang.strip() or "cn"
        env["PHONE_AGENT_WDA_URL"] = self.config.wda_url.strip() or "http://localhost:8100"
        if resolved_device_id:
            env["PHONE_AGENT_DEVICE_ID"] = resolved_device_id

        cmd = [
            self._python_executable(),
            "-m",
            "lampgo.vendor.open_autoglm.runner",
            "--device-type",
            self.config.device_type.strip().lower(),
            "--max-steps",
            str(resolved_steps),
            "--base-url",
            self.llm_config.api_base.strip(),
            "--model",
            self.llm_config.model.strip(),
            "--lang",
            self.config.lang.strip() or "cn",
        ]
        if resolved_device_id:
            cmd.extend(["--device-id", resolved_device_id])
        if self.config.device_type.strip().lower() == "ios":
            cmd.extend(["--wda-url", self.config.wda_url.strip() or "http://localhost:8100"])
        if allow_sensitive:
            cmd.append("--allow-sensitive")
        if self.config.auto_install_adb_keyboard:
            cmd.append("--auto-install-adb-keyboard")
        cmd.append(task)

        started = time.monotonic()
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=resolved_timeout)
        except TimeoutError:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            return PhoneTaskResult(
                ok=False,
                status="timeout",
                duration_s=round(time.monotonic() - started, 3),
                error=f"phone task timed out after {resolved_timeout:.1f}s",
            )
        except Exception as exc:
            return PhoneTaskResult(
                ok=False,
                status="error",
                duration_s=round(time.monotonic() - started, 3),
                error=str(exc),
            )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        ok = proc.returncode == 0
        return PhoneTaskResult(
            ok=ok,
            status="ok" if ok else "error",
            duration_s=round(time.monotonic() - started, 3),
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            error="" if ok else (stderr.strip() or _tail(stdout)),
        )


def _tail(text: str, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[-limit:]
