from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from starlette.testclient import TestClient

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.expression_clips import create_expression_clip
from lampgo.expression_library import (
    ExpressionLibraryError,
    expression_capabilities,
    list_expression_presets,
    list_led_effects,
    resolve_expression,
    save_expression_preset,
    save_led_effect,
)
from lampgo.led_effects import LEF_MAGIC
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


def _sprite_sheet(*, rows: int = 3, cols: int = 10) -> bytes:
    cv2 = pytest.importorskip("cv2")
    sheet = np.zeros((rows * 12, cols * 20, 3), dtype=np.uint8)
    for index in range(rows * cols):
        row = index // cols
        col = index % cols
        sheet[row * 12 + 2 : row * 12 + 10, col * 20 + 3 : col * 20 + 17] = (index * 5, 220, 255)
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
    assert ok
    return bytes(encoded)


def _create_eye(clip_id: str, *, default_led_effect_id: str | None = None) -> dict:
    return create_expression_clip(
        clip_id=clip_id,
        expression=clip_id,
        source_bytes=_sprite_sheet(),
        filename=f"{clip_id}.png",
        content_type="image/png",
        fps=10,
        grid_rows=3,
        grid_cols=10,
        default_led_effect_id=default_led_effect_id,
    )


def _custom_effect(effect_id: str) -> dict:
    return save_led_effect(
        {
            "effect_id": effect_id,
            "label": effect_id,
            "role": "mouth",
            "program": {
                "version": 1,
                "template": "mouth",
                "variant": "smile",
                "defaults": {"color": "#ffffff", "intensity": 0.8},
            },
        }
    )


def _pixel_effect(effect_id: str) -> dict:
    rows = ["." * 51 for _ in range(9)]
    rows[4] = "." * 10 + "1" * 31 + "." * 10
    return save_led_effect(
        {
            "effect_id": effect_id,
            "label": "彩色嘴巴",
            "role": "mouth",
            "program": {
                "version": 2,
                "type": "pixel_clip",
                "fps": 10,
                "palette": {".": "#000000", "1": "#ff0088"},
                "roles": {"primary": "1"},
                "frames": [{"rows": rows, "ticks": 30}],
            },
        }
    )


def _gateway(monkeypatch, tmp_path: Path) -> WebGateway:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    return WebGateway(LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null"))))


def test_eye_default_led_and_explicit_none(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    _create_eye("focused_eyes", default_led_effect_id="heart")

    inherited = resolve_expression({"eye_clip_id": "focused_eyes"})
    assert inherited["led_effect_id"] == "heart"

    eye_only = resolve_expression({"eye_clip_id": "focused_eyes", "led_effect_id": None})
    assert eye_only["led_effect_id"] is None
    assert eye_only["eye_storage_clip_id"] == "focused_eyes"


def test_many_to_many_presets_reuse_assets(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    _create_eye("calm_eyes")
    _create_eye("happy_eyes")
    _custom_effect("soft_mouth")

    save_expression_preset(
        {
            "preset_id": "calm_smile",
            "eye_clip_id": "calm_eyes",
            "led_effect_id": "soft_mouth",
            "playback": "once",
        }
    )
    save_expression_preset(
        {
            "preset_id": "happy_smile",
            "eye_clip_id": "happy_eyes",
            "led_effect_id": "soft_mouth",
            "playback": "once",
        }
    )

    presets = list_expression_presets()
    assert {item["preset_id"] for item in presets} == {"calm_smile", "happy_smile"}
    assert {item["led_effect_id"] for item in presets} == {"soft_mouth"}
    assert [item["effect_id"] for item in list_led_effects()].count("soft_mouth") == 1


def test_codex_led_effect_is_available_without_an_eye(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))

    effect = next(item for item in list_led_effects() if item["effect_id"] == "codex")
    resolved = resolve_expression({"led_effect_id": "codex", "playback": "loop"})

    assert effect["label"] == "CODEX 闪烁字标"
    assert effect["animated"] is True
    assert effect["program"] == {
        "version": 1,
        "template": "codex",
        "defaults": {
            "color": "#f4f4f4",
            "secondary_color": "#00d8ff",
            "brightness": 64,
            "intensity": 1.0,
        },
    }
    assert resolved["eye_clip_id"] is None
    assert resolved["led_effect_id"] == "codex"
    assert resolved["led_effect"]["program"]["template"] == "codex"
    assert resolved["playback"] == "loop"


def test_dizzy_legacy_asset_migrates_without_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    _create_eye("dizzy")

    resolved = resolve_expression({"preset_id": "dizzy"})
    assert resolved["eye_clip_id"] == "dizzy_eyes"
    assert resolved["eye_storage_clip_id"] == "dizzy"
    assert resolved["led_effect_id"] == "dizzy_mouth"
    assert resolved["playback"] == "loop"


def test_preset_api_requires_confirmation_and_transient_play_does_not_save(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    _create_eye("focused_eyes")
    _custom_effect("soft_mouth")
    sent: list[tuple[str, dict]] = []

    async def fake_proxy_post(path: str, payload: dict):
        sent.append((path, payload))
        return 200, {"ok": True}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_post", fake_proxy_post)
    composition = {
        "eye_clip_id": "focused_eyes",
        "led_effect_id": "soft_mouth",
        "led_params": {"color": "#ff00aa", "brightness": 64, "intensity": 0.8},
        "playback": "once",
    }

    with TestClient(gateway.app) as client:
        played = client.post("/api/expressions/play", json=composition)
        assert played.status_code == 200
        assert list_expression_presets() == []

        rejected = client.post("/api/expression-presets", json={"preset_id": "focus", **composition})
        assert rejected.status_code == 400
        assert "confirmed=true" in rejected.json()["error"]

        rejected_update = client.put("/api/expression-presets/focus", json=composition)
        assert rejected_update.status_code == 400
        assert "confirmed=true" in rejected_update.json()["error"]

        saved = client.post(
            "/api/expression-presets",
            json={"preset_id": "focus", "label": "Focus", "confirmed": True, **composition},
        )
        assert saved.status_code == 200

    assert sent[0][0] == "/device/expressions/play"
    assert sent[0][1]["eye_clip_id"] == "focused_eyes"
    assert sent[0][1]["led_effect_id"] == "soft_mouth"
    assert sent[0][1]["led_program"]["template"] == "mouth"
    assert [item["preset_id"] for item in list_expression_presets()] == ["focus"]


def test_led_only_play_keeps_eye_channel_untouched(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    sent: list[tuple[str, dict]] = []

    async def fake_proxy_post(path: str, payload: dict):
        sent.append((path, payload))
        return 200, {"ok": True}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_post", fake_proxy_post)

    with TestClient(gateway.app) as client:
        played = client.post(
            "/api/expressions/play",
            json={
                "eye_clip_id": None,
                "led_effect_id": "heart",
                "playback": "loop",
                "duration_ms": 3000,
            },
        )

    assert played.status_code == 200
    assert sent[0][0] == "/device/expressions/play"
    assert sent[0][1]["eye_clip_id"] is None
    assert sent[0][1]["led_effect_id"] == "heart"
    assert sent[0][1]["playback"] == "loop"


def test_eye_default_led_api(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    _create_eye("focused_eyes")
    _custom_effect("soft_mouth")

    with TestClient(gateway.app) as client:
        updated = client.post(
            "/api/eyes/focused_eyes",
            json={"default_led_effect_id": "soft_mouth"},
        )
        assert updated.status_code == 200
        assert updated.json()["result"]["eye"]["default_led_effect_id"] == "soft_mouth"

    resolved = resolve_expression({"eye_clip_id": "focused_eyes"})
    assert resolved["led_effect_id"] == "soft_mouth"


def test_led_dsl_and_capacity_guards(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    with pytest.raises(ExpressionLibraryError, match="program.template"):
        save_led_effect(
            {
                "effect_id": "unsafe",
                "role": "accent",
                "program": {"version": 1, "template": "python", "defaults": {}},
            }
        )

    _custom_effect("safe_mouth")
    with pytest.raises(ExpressionLibraryError, match="brightness"):
        resolve_expression({"led_effect_id": "safe_mouth", "led_params": {"brightness": 97}})
    capacity = expression_capabilities()
    assert capacity["led_effects"]["installed_custom"] == 1
    assert capacity["led_effects"]["single_max_bytes"] == 8 * 1024
    assert capacity["presets"]["max_count"] == 64


def test_pixel_led_auto_syncs_then_plays_by_id_with_loop_default(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    _pixel_effect("color_mouth")
    uploads: list[tuple[str, bytes, dict]] = []
    plays: list[tuple[str, dict]] = []

    async def fake_proxy_get(path: str):
        assert path == "/device/led-effects"
        return 200, {"ok": True, "result": {"led_effects": []}}, "application/json"

    async def fake_proxy_post_bytes(path: str, payload: bytes, *, params=None, content_type="application/octet-stream"):
        uploads.append((path, payload, params or {}))
        return 200, {"ok": True, "action": "upload"}, "application/json"

    async def fake_proxy_post(path: str, payload: dict):
        plays.append((path, payload))
        return 200, {"ok": True}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_get", fake_proxy_get)
    monkeypatch.setattr(gateway.server.esp32, "proxy_post_bytes", fake_proxy_post_bytes)
    monkeypatch.setattr(gateway.server.esp32, "proxy_post", fake_proxy_post)

    with TestClient(gateway.app) as client:
        response = client.post("/api/expressions/play", json={"led_effect_id": "color_mouth"})
        catalog = client.get("/api/expression-catalog")

    assert response.status_code == 200
    assert uploads[0][0] == "/device/led-effects/upload"
    assert uploads[0][1].startswith(LEF_MAGIC)
    assert uploads[0][2]["effect_id"] == "color_mouth"
    assert plays[0][0] == "/device/expressions/play"
    assert plays[0][1]["led_effect_id"] == "color_mouth"
    assert plays[0][1]["playback"] == "loop"
    assert "led_program" not in plays[0][1]
    assert catalog.status_code == 200
    assert any(
        item["effect_id"] == "color_mouth"
        for item in catalog.json()["result"]["led_effects"]
    )


def test_preset_name_can_generate_stable_machine_id(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    _pixel_effect("color_mouth")
    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/expression-presets",
            json={
                "name": "我的彩色笑脸",
                "led_effect_id": "color_mouth",
                "confirmed": True,
            },
        )
    assert response.status_code == 200
    preset = response.json()["result"]["preset"]
    assert preset["preset_id"].startswith("usr_")
    assert preset["label"] == "我的彩色笑脸"
    assert preset["playback"] == "loop"
