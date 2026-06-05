from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from lampgo.core.config import CameraConfig, LLMConfig, PhoneAgentConfig
from lampgo.device.phone_agent import PhoneTaskResult
from lampgo.device.phone_direct import DirectPhoneResult, plan_direct_phone_task
from lampgo.skills.builtin.phone_skills import PhoneTaskSkill


class FakeHttpClient:
    calls: list[str] = []

    def __init__(self, **kwargs: Any) -> None:
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is True

    def __enter__(self) -> FakeHttpClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str):
        self.calls.append(url)
        if "/switch" in url:
            facing = "front" if "facing=front" in url else "back"
            return FakeResponse(200, {"ok": True, "requested_facing": facing, "active_facing": facing})
        return FakeResponse(
            200,
            {
                "ok": True,
                "camera_started": True,
                "front_available": True,
                "back_available": True,
            },
        )


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_phone_task_switches_front_camera_from_task(monkeypatch) -> None:
    FakeHttpClient.calls = []
    monkeypatch.setattr(httpx, "Client", FakeHttpClient)
    skill = PhoneTaskSkill(
        PhoneAgentConfig(),
        LLMConfig(),
        CameraConfig(port="http://127.0.0.1:18765/snapshot.jpg"),
    )

    result = await skill.execute(SimpleNamespace(), task="切换到前置摄像头")

    assert result.status == "ok"
    assert result.data["requested_facing"] == "front"
    assert FakeHttpClient.calls[0] == "http://127.0.0.1:18765/switch?facing=front"
    assert FakeHttpClient.calls[1] == "http://127.0.0.1:18765/health"


@pytest.mark.asyncio
async def test_phone_task_switches_back_camera_from_explicit_param(monkeypatch) -> None:
    FakeHttpClient.calls = []
    monkeypatch.setattr(httpx, "Client", FakeHttpClient)
    skill = PhoneTaskSkill(
        PhoneAgentConfig(),
        LLMConfig(),
        CameraConfig(port="http://127.0.0.1:18765/snapshot.jpg"),
    )

    result = await skill.execute(SimpleNamespace(), camera_facing="back")

    assert result.status == "ok"
    assert result.message == "phone camera switched to back"
    assert result.data["switch_response"]["active_facing"] == "back"
    assert FakeHttpClient.calls[0] == "http://127.0.0.1:18765/switch?facing=back"


@pytest.mark.asyncio
async def test_phone_task_camera_switch_requires_companion_camera_url() -> None:
    skill = PhoneTaskSkill(PhoneAgentConfig(), LLMConfig(), CameraConfig(port="0"))

    result = await skill.execute(SimpleNamespace(), camera_facing="front")

    assert result.status == "error"
    assert "camera.port" in result.message


def test_direct_phone_task_parser_launches_simple_app_tasks() -> None:
    command = plan_direct_phone_task("请打开系统设置应用。优先使用 Launch 操作，app 参数使用 Settings。如果已经打开设置，请 finish。")

    assert command is not None
    assert command.kind == "launch"
    assert command.value == "Settings"


def test_direct_phone_task_parser_handles_home_task() -> None:
    command = plan_direct_phone_task("返回手机桌面")

    assert command is not None
    assert command.kind == "home"


def test_direct_phone_task_parser_launches_camera_for_selfie_setup() -> None:
    command = plan_direct_phone_task("假设手机支架已经转向用户，请打开手机相机应用，为自拍做准备，不要拍照。")

    assert command is not None
    assert command.kind == "launch"
    assert command.value == "com.oplus.camera"


def test_direct_phone_task_parser_leaves_complex_tasks_for_gui_agent() -> None:
    command = plan_direct_phone_task("打开微信然后搜索天气")

    assert command is None


@pytest.mark.asyncio
async def test_phone_task_uses_direct_adb_for_simple_launch(monkeypatch) -> None:
    def fake_direct(*args: Any, **kwargs: Any) -> DirectPhoneResult:
        return DirectPhoneResult(
            ok=True,
            status="ok",
            action="launch",
            duration_s=0.12,
            message="directly launched Settings",
            data={"app": "Settings", "package": "com.android.settings"},
        )

    async def fail_agent(*args: Any, **kwargs: Any) -> PhoneTaskResult:
        raise AssertionError("Open-AutoGLM should not run for a direct launch")

    monkeypatch.setattr("lampgo.skills.builtin.phone_skills.run_direct_phone_task", fake_direct)
    monkeypatch.setattr("lampgo.device.phone_agent.PhoneAgentRunner.run_task", fail_agent)

    skill = PhoneTaskSkill(
        PhoneAgentConfig(enabled=True, verify_result=False),
        LLMConfig(api_base="http://localhost:8000/v1"),
    )

    result = await skill.execute(SimpleNamespace(), task="打开设置")

    assert result.status == "ok"
    assert result.message == "directly launched Settings"
    assert result.data["backend"] == "direct_adb"
    assert result.data["direct"]["data"]["package"] == "com.android.settings"


@pytest.mark.asyncio
async def test_phone_task_falls_back_to_gui_agent_for_complex_task(monkeypatch) -> None:
    def fake_direct(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_agent(*args: Any, **kwargs: Any) -> PhoneTaskResult:
        return PhoneTaskResult(ok=True, status="ok", duration_s=1.5, returncode=0, stdout="Parsing action: Launch")

    monkeypatch.setattr("lampgo.skills.builtin.phone_skills.run_direct_phone_task", fake_direct)
    monkeypatch.setattr("lampgo.device.phone_agent.PhoneAgentRunner.run_task", fake_agent)

    skill = PhoneTaskSkill(
        PhoneAgentConfig(enabled=True, verify_result=False),
        LLMConfig(api_base="http://localhost:8000/v1"),
    )

    result = await skill.execute(SimpleNamespace(), task="打开微信然后搜索天气")

    assert result.status == "ok"
    assert result.data["backend"] == "open_autoglm"
    assert result.data["diagnostics"]["action_count"] == 1
