from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from lampgo.core.config import CameraConfig, LLMConfig, PhoneAgentConfig
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
