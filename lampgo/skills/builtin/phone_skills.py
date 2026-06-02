"""Phone-control skills backed by Open-AutoGLM."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from lampgo.core.config import CameraConfig, LLMConfig, PhoneAgentConfig
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
        "unless the user explicitly approved that action. It can also switch the Lampgo Android "
        "camera companion between front and back cameras."
    )
    parameters = {
        "task": ParameterSpec(
            name="task",
            type="str",
            required=False,
            default="",
            description="Natural-language instruction for the phone agent.",
        ),
        "camera_facing": ParameterSpec(
            name="camera_facing",
            type="str",
            required=False,
            default="",
            description="Optional direct Android camera companion switch: front or back.",
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

    def __init__(
        self,
        config: PhoneAgentConfig,
        llm_config: LLMConfig,
        camera_config: CameraConfig | None = None,
    ) -> None:
        self._config = config
        self._llm_config = llm_config
        self._camera_config = camera_config or CameraConfig()

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        task = str(params.get("task") or "").strip()
        camera_facing = _extract_camera_facing(params, task)
        if camera_facing is not None:
            return await asyncio.to_thread(self._switch_companion_camera, camera_facing)

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
                adb_path=self._config.adb_path,
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

    def _switch_companion_camera(self, facing: str) -> SkillResult:
        base_url = _companion_base_url(self._camera_config.port)
        if base_url is None:
            return SkillResult(
                status="error",
                message=(
                    "phone camera switch requires camera.port to be an Android companion "
                    "HTTP URL such as http://127.0.0.1:18765/snapshot.jpg"
                ),
                data={"requested_facing": facing, "camera_port": self._camera_config.port},
            )
        try:
            import httpx
        except ImportError:
            return SkillResult(status="error", message="httpx is required to switch the phone camera")

        switch_url = f"{base_url}/switch?{urlencode({'facing': facing})}"
        health_url = f"{base_url}/health"
        try:
            with httpx.Client(timeout=8.0, trust_env=False, follow_redirects=True) as client:
                switch_resp = client.get(switch_url)
                switch_payload = _json_or_text(switch_resp)
                if switch_resp.status_code != 200:
                    return SkillResult(
                        status="error",
                        message=f"phone camera switch failed: HTTP {switch_resp.status_code}",
                        data={
                            "action": "phone_camera_switch",
                            "requested_facing": facing,
                            "switch_url": switch_url,
                            "switch_response": switch_payload,
                        },
                    )
                try:
                    health_resp = client.get(health_url)
                    health_payload = _json_or_text(health_resp)
                except Exception as exc:  # noqa: BLE001
                    health_payload = {"error": str(exc)}
            return SkillResult(
                status="ok",
                message=f"phone camera switched to {facing}",
                data={
                    "action": "phone_camera_switch",
                    "requested_facing": facing,
                    "switch_url": switch_url,
                    "switch_response": switch_payload,
                    "health": health_payload,
                },
            )
        except httpx.RequestError as exc:
            return SkillResult(
                status="error",
                message=f"phone camera switch request failed: {exc}",
                data={"action": "phone_camera_switch", "requested_facing": facing, "switch_url": switch_url},
            )


def _tail(text: str, limit: int = 3000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _extract_camera_facing(params: dict[str, Any], task: str) -> str | None:
    explicit = _normalize_camera_facing(str(params.get("camera_facing") or ""))
    if explicit is not None:
        return explicit

    text = task.strip().lower()
    if not text:
        return None
    wants_camera = any(token in text for token in ["摄像头", "相机", "camera", "镜头"])
    if not wants_camera:
        return None

    front = any(token in text for token in ["前置", "前摄", "自拍", "front", "selfie"])
    back = any(token in text for token in ["后置", "后摄", "主摄", "rear", "back"])
    if front and not back:
        return "front"
    if back and not front:
        return "back"
    return None


def _normalize_camera_facing(value: str) -> str | None:
    text = value.strip().lower()
    if text in {"front", "前置", "前摄", "selfie"}:
        return "front"
    if text in {"back", "rear", "后置", "后摄", "主摄"}:
        return "back"
    return None


def _companion_base_url(camera_port: str) -> str | None:
    parsed = urlparse(camera_port.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")


def _json_or_text(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        text = getattr(resp, "text", "")
        return text[:1000] if isinstance(text, str) else str(text)[:1000]


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
