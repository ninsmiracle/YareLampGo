"""Phone-control skills backed by Open-AutoGLM."""

from __future__ import annotations

import re
from typing import Any

from lampgo.core.config import LLMConfig, PhoneAgentConfig
from lampgo.core.types import SkillResult
from lampgo.device.phone_agent import PhoneAgentRunner
from lampgo.device.phone_observer import capture_phone_observation, verify_phone_task_result
from lampgo.skills.base import ParameterSpec, Skill, SkillContext


class PhoneTaskSkill(Skill):
    skill_id = "phone_task"
    description = (
        "Control the mounted Android phone using natural language via Open-AutoGLM. "
        "Use this for phone UI tasks such as opening apps, searching, tapping, typing, "
        "or browsing. Do not use it for payments, irreversible deletion, or sending messages "
        "unless the user explicitly approved that action."
    )
    parameters = {
        "task": ParameterSpec(
            name="task",
            type="str",
            description="Natural-language instruction for the phone agent.",
        ),
        "max_steps": ParameterSpec(
            name="max_steps",
            type="int",
            required=False,
            default=10,
            description="Maximum phone-agent GUI steps for this task.",
        ),
        "device_id": ParameterSpec(
            name="device_id",
            type="str",
            required=False,
            description="Optional ADB device id override.",
        ),
        "timeout_s": ParameterSpec(
            name="timeout_s",
            type="int",
            required=False,
            description="Optional timeout in seconds.",
        ),
        "allow_sensitive": ParameterSpec(
            name="allow_sensitive",
            type="bool",
            required=False,
            default=False,
            description="True only after explicit user approval for sensitive phone actions.",
        ),
        "verify_result": ParameterSpec(
            name="verify_result",
            type="bool",
            required=False,
            default=True,
            description="Capture a final screenshot and verify the phone result after execution.",
        ),
    }

    def __init__(self, config: PhoneAgentConfig, llm_config: LLMConfig) -> None:
        self._config = config
        self._llm_config = llm_config

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        task = str(params.get("task") or "").strip()
        if not task:
            return SkillResult(status="error", message="phone_task requires a task")

        device_id = str(params.get("device_id") or "").strip() or self._config.device_id
        original_task = task

        if not bool(params.get("allow_sensitive", False)):
            task = (
                f"{task}\n\n"
                "安全约束：不要付款、下单、删除内容、发送消息、提交表单或确认敏感操作；"
                "遇到这些步骤请停止并说明需要用户确认。"
            )
        task = _augment_task_for_result_check(task)

        max_steps = params.get("max_steps")
        timeout_s = params.get("timeout_s")
        runner = PhoneAgentRunner(self._config, self._llm_config)
        result = await runner.run_task(
            task,
            max_steps=int(max_steps) if max_steps is not None else None,
            device_id=device_id or None,
            timeout_s=float(timeout_s) if timeout_s is not None else None,
            allow_sensitive=bool(params.get("allow_sensitive", False)),
        )

        data = {
            "status": result.status,
            "duration_s": result.duration_s,
            "returncode": result.returncode,
            "diagnostics": _diagnose_stdout(result.stdout),
            "stdout_tail": _tail(result.stdout),
            "stderr_tail": _tail(result.stderr),
        }
        should_verify = bool(params.get("verify_result", self._config.verify_result)) and self._config.verify_result
        if should_verify:
            observation = capture_phone_observation(
                task=original_task,
                device_id=device_id,
                device_type=self._config.device_type,
                artifact_dir=self._config.artifact_dir,
            )
            verification = verify_phone_task_result(original_task, observation)
            data["observation"] = observation.to_dict()
            data["verification"] = verification
            if result.ok and not verification.get("ok", False):
                return SkillResult(
                    status="error",
                    message=f"phone task finished but result verification failed: {verification.get('reasons')}",
                    data=data,
                )

        if result.ok:
            return SkillResult(status="ok", data=data)
        return SkillResult(status="error", message=result.error or result.status, data=data)


def _tail(text: str, limit: int = 3000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _augment_task_for_result_check(task: str) -> str:
    return (
        f"{task}\n\n"
        "结果要求：完成前请确认最终手机屏幕不是空白页，且界面内容与任务目标一致；"
        "如果任务包含搜索或输入关键词，请确保关键词出现在最终界面上，然后再 finish。"
    )


def _diagnose_stdout(stdout: str) -> dict[str, Any]:
    text = stdout or ""
    inference_times = [
        float(value)
        for value in re.findall(r"(?:总推理时间|Total Inference Time)[^\d]*(\d+(?:\.\d+)?)s", text)
    ]
    ttft_times = [
        float(value)
        for value in re.findall(r"(?:首 Token 延迟 \(TTFT\)|Time to First Token \(TTFT\))[^\d]*(\d+(?:\.\d+)?)s", text)
    ]
    action_lines = re.findall(r"Parsing action:\s*(.+)", text)
    return {
        "skip_model_check": "Skipping model API check" in text,
        "model_api_check": "Checking model API" in text,
        "model_call_count": len(inference_times),
        "model_total_inference_s": round(sum(inference_times), 3),
        "model_max_inference_s": max(inference_times) if inference_times else 0.0,
        "model_ttft_s": round(sum(ttft_times), 3),
        "action_count": len(action_lines),
        "actions": action_lines[-10:],
        "max_steps_reached": "Max steps reached" in text,
    }
