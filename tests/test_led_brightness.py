from __future__ import annotations

from types import SimpleNamespace

import pytest

from lampgo.core.config import LEDConfig
from lampgo.core.led import LEDController
from lampgo.skills.builtin.expression_skills import SetExpressionSkill


def _controller_with_capture(ceiling: dict[str, int]):
    sent: list[tuple[str, dict, str]] = []
    controller = LEDController(
        LEDConfig(port=""),
        brightness_ceiling=lambda: ceiling["value"],
    )

    def capture(path: str, payload: dict, *, reason: str) -> bool:
        sent.append((path, payload, reason))
        return True

    controller._send_remote_path = capture  # type: ignore[method-assign]
    return controller, sent


def test_led_controller_caps_direct_and_dynamic_expression_brightness(monkeypatch):
    ceiling = {"value": 16}
    controller, sent = _controller_with_capture(ceiling)

    assert controller.set_brightness(96) is True
    assert sent[-1][1]["brightness"] == 16
    assert controller.set_brightness(8) is True
    assert sent[-1][1]["brightness"] == 8

    def fake_resolve(_request: dict):
        return {
            "eye_storage_clip_id": "cat-eyes",
            "led_effect_id": "blue-pixel-cat",
            "led_params": {"brightness": 64},
            "playback": "once",
            "duration_ms": 3000,
            "led_effect": {"kind": "pixel_clip"},
        }

    monkeypatch.setattr("lampgo.expression_library.resolve_expression", fake_resolve)

    ok, composition = controller.play_expression("pixel-cat")
    assert ok is True
    assert composition is not None
    assert composition["led_params"]["brightness"] == 16
    assert sent[-1][0] == "/device/expressions/play"
    assert sent[-1][1]["led_params"]["brightness"] == 16

    ceiling["value"] = 8
    ok, composition = controller.play_expression("pixel-cat")
    assert ok is True
    assert composition is not None
    assert composition["led_params"]["brightness"] == 8
    assert sent[-1][1]["led_params"]["brightness"] == 8


def test_led_controller_uses_p4_composite_endpoint_for_builtin_modes():
    ceiling = {"value": 16}
    controller, sent = _controller_with_capture(ceiling)
    controller.bind_esp32_manager(
        SimpleNamespace(get_status=lambda: {"device": {"platform": "esp32-p4"}})
    )

    assert controller.set_mode("check") is True

    assert sent == [
        (
            "/device/expressions/play",
            {
                "expression": "check",
                "led_mode": 15,
                "led_params": {"brightness": 16},
                "playback": "loop",
            },
            "expression_play",
        )
    ]


@pytest.mark.asyncio
async def test_set_expression_builtin_route_uses_runtime_brightness_ceiling():
    ceiling = {"value": 16}
    controller, sent = _controller_with_capture(ceiling)
    context = SimpleNamespace(led=controller)

    result = await SetExpressionSkill().execute(
        context,
        expression="smiley",
        brightness=96,
    )

    assert result.status == "ok"
    assert sent[0][0] == "/device/led"
    assert sent[0][1]["brightness"] == 16
    assert sent[1][0] == "/device/led"
    assert sent[1][1]["mode"] == 21
