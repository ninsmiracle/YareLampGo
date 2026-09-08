from __future__ import annotations

import base64
import struct
from pathlib import Path

import numpy as np
import pytest
from starlette.testclient import TestClient

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.expression_clips import (
    create_expression_clip,
    list_expression_clips,
    update_expression_clip_sync,
)
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


def _png_sprite_sheet(*, rows: int = 3, cols: int = 10, cell_w: int = 32, cell_h: int = 18) -> bytes:
    cv2 = pytest.importorskip("cv2")
    sheet = np.zeros((rows * cell_h, cols * cell_w, 3), dtype=np.uint8)
    for index in range(rows * cols):
        row = index // cols
        col = index % cols
        x0 = col * cell_w
        y0 = row * cell_h
        color = np.array([(index * 7) % 255, 210, 255 - ((index * 5) % 180)], dtype=np.uint8)
        sheet[y0 + 3 : y0 + cell_h - 3, x0 + 4 : x0 + cell_w - 4] = color
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
    assert ok
    return bytes(encoded)


def _make_gateway(monkeypatch, tmp_path: Path) -> WebGateway:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    return WebGateway(server)


def test_create_expression_clip_from_sprite_sheet(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))

    manifest = create_expression_clip(
        clip_id="smiley",
        expression="smiley",
        source_bytes=_png_sprite_sheet(),
        filename="smiley.png",
        content_type="image/png",
        fps=10,
        grid_rows=3,
        grid_cols=10,
    )

    assert manifest["clip_id"] == "smiley"
    assert manifest["duration_ms"] == 3000
    assert manifest["frame_count"] == 30
    assert manifest["lcd"]["bytes"] > 0
    assert manifest["led"] == {"type": "procedural", "effect": "smiley"}
    assert (tmp_path / "expression_clips" / "smiley" / "lcd.bin").exists()
    assert not (tmp_path / "expression_clips" / "smiley" / "led.bin").exists()
    assert list_expression_clips()[0]["expression"] == "smiley"


def test_expression_clip_rejects_short_duration(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))

    with pytest.raises(ValueError, match="duration"):
        create_expression_clip(
            clip_id="too-short",
            expression="smiley",
            source_bytes=_png_sprite_sheet(rows=1, cols=5),
            filename="short.png",
            fps=10,
            grid_rows=1,
            grid_cols=5,
        )


def test_expression_clip_rejects_id_that_exceeds_s3_spiffs_filename_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))

    with pytest.raises(ValueError, match="1-13 chars"):
        create_expression_clip(
            clip_id="pixel-cat-eyes",
            expression="pixel-cat",
            source_bytes=_png_sprite_sheet(),
            filename="pixel-cat.png",
            fps=10,
            grid_rows=3,
            grid_cols=10,
        )


def test_create_expression_clip_supports_precise_two_second_30fps_performance(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))

    manifest = create_expression_clip(
        clip_id="ecstatic-eyes",
        expression="ecstatic",
        source_bytes=_png_sprite_sheet(rows=6, cols=10),
        filename="ecstatic.png",
        content_type="image/png",
        fps=30,
        grid_rows=6,
        grid_cols=10,
        default_led_effect_id="ecstatic-mouth",
    )

    payload = (tmp_path / "expression_clips" / "ecstatic-eyes" / "lcd.bin").read_bytes()
    _, _, frame_count, fps = struct.unpack_from("<HHHH", payload, 6)
    offset = 14
    durations: list[int] = []
    for _ in range(frame_count):
        _, _, _, _, frame_duration_ms, run_count = struct.unpack_from("<HHHHHH", payload, offset)
        durations.append(frame_duration_ms)
        offset += 12 + run_count * 4

    assert manifest["duration_ms"] == 2000
    assert manifest["frame_count"] == 60
    assert manifest["led"]["effect"] == "ecstatic-mouth"
    assert manifest["default_led_effect_id"] == "ecstatic-mouth"
    assert fps == 30
    assert set(durations) == {33, 34}
    assert sum(durations) == 2000


def test_expression_clip_accepts_a_six_second_source_window(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))

    manifest = create_expression_clip(
        clip_id="long-eyes",
        expression="long",
        source_bytes=_png_sprite_sheet(),
        filename="long.png",
        content_type="image/png",
        fps=10,
        duration_s=6.0,
        grid_rows=3,
        grid_cols=10,
    )

    assert manifest["duration_ms"] == 6000


def test_rebuilding_identical_eye_preserves_sync_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    source = _png_sprite_sheet()
    create_expression_clip(
        clip_id="stable-eyes",
        expression="stable",
        source_bytes=source,
        filename="stable.png",
        content_type="image/png",
        fps=10,
        grid_rows=3,
        grid_cols=10,
        default_led_effect_id="soft-mouth",
    )
    update_expression_clip_sync(
        "stable-eyes",
        status="synced",
        device={"c6_confirmed": True},
    )

    rebuilt = create_expression_clip(
        clip_id="stable-eyes",
        expression="stable",
        source_bytes=source,
        filename="stable.png",
        content_type="image/png",
        fps=10,
        grid_rows=3,
        grid_cols=10,
        default_led_effect_id="soft-mouth",
    )

    assert rebuilt["sync"]["status"] == "synced"
    assert rebuilt["sync"]["last_synced_at"] is not None
    assert rebuilt["sync"]["device"] == {"c6_confirmed": True}


def test_expression_clip_api_upload_and_sync(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)
    sent: list[tuple[str, bytes, dict[str, object]]] = []

    async def fake_proxy_post_bytes(
        path: str,
        payload: bytes,
        *,
        params: dict[str, object] | None = None,
        content_type: str = "application/octet-stream",
    ):
        sent.append((path, payload, params or {}))
        return 200, {"ok": True, "action": "upload", "c6_confirmed": True}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_post_bytes", fake_proxy_post_bytes)

    upload = {
        "clip_id": "focused",
        "expression": "focused",
        "filename": "focused.png",
        "content_type": "image/png",
        "content_base64": base64.b64encode(_png_sprite_sheet()).decode("ascii"),
        "fps": 10,
        "grid_rows": 3,
        "grid_cols": 10,
    }
    with TestClient(gateway.app) as client:
        response = client.post("/api/expression-clips", json=upload)
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["result"]["clip"]["clip_id"] == "focused"

        listed = client.get("/api/expression-clips").json()
        assert listed["result"]["clips"][0]["clip_id"] == "focused"

        sync = client.post("/api/device/expression-clips/sync", json={"clip_id": "focused"})
        assert sync.status_code == 200
        result = sync.json()["result"]
        assert result["transfer_mode"] == "bulk"
        assert result["sent_chunks"] == 1
        assert result["device_body"]["c6_confirmed"] is True

    assert len(sent) == 1
    assert sent[0][0] == "/device/expression-clips/upload"
    assert sent[0][1]
    assert sent[0][2]["clip_id"] == "focused"
    assert sent[0][2]["led_effect"] == "focused"
    assert sent[0][2]["owner_id"] == gateway.server.esp32.owner_id


def test_expression_clip_sync_rejects_missing_c6_confirmation(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)
    create_expression_clip(
        clip_id="focused",
        expression="focused",
        source_bytes=_png_sprite_sheet(),
        filename="focused.png",
        content_type="image/png",
        fps=10,
        grid_rows=3,
        grid_cols=10,
    )

    async def fake_proxy_post_bytes(*_args, **_kwargs):
        return 200, {"ok": True, "action": "upload"}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_post_bytes", fake_proxy_post_bytes)

    with TestClient(gateway.app) as client:
        response = client.post("/api/device/expression-clips/sync", json={"clip_id": "focused"})

    assert response.status_code == 502
    assert response.json()["error"] == "device display did not confirm clip sync"


def test_expression_clip_sync_accepts_direct_p4_display_confirmation(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)
    create_expression_clip(
        clip_id="p4-direct",
        expression="focused",
        source_bytes=_png_sprite_sheet(),
        filename="focused.png",
        content_type="image/png",
        fps=10,
        grid_rows=3,
        grid_cols=10,
    )

    async def fake_proxy_post_bytes(*_args, **_kwargs):
        return 200, {"ok": True, "display_confirmed": True}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_post_bytes", fake_proxy_post_bytes)
    with TestClient(gateway.app) as client:
        response = client.post("/api/device/expression-clips/sync", json={"clip_id": "p4-direct"})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_expression_play_uploads_eye_to_p4_before_playing(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)
    create_expression_clip(
        clip_id="p4-play",
        expression="focused",
        source_bytes=_png_sprite_sheet(),
        filename="focused.png",
        content_type="image/png",
        fps=10,
        grid_rows=3,
        grid_cols=10,
    )
    calls: list[str] = []

    async def fake_proxy_post_bytes(path, *_args, **_kwargs):
        calls.append(path)
        return 200, {"ok": True, "display_confirmed": True}, "application/json"

    async def fake_proxy_post(path, *_args, **_kwargs):
        calls.append(path)
        return 200, {"ok": True, "display_confirmed": True}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_post_bytes", fake_proxy_post_bytes)
    monkeypatch.setattr(gateway.server.esp32, "proxy_post", fake_proxy_post)

    with TestClient(gateway.app) as client:
        response = client.post("/api/expressions/play", json={"eye_clip_id": "p4-play"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == ["/device/expression-clips/upload", "/device/expressions/play"]


def test_expression_play_keeps_legacy_s3_c6_upload_contract(monkeypatch, tmp_path):
    """The P4 direct-display path must not replace the S3-to-C6 protocol."""
    gateway = _make_gateway(monkeypatch, tmp_path)
    create_expression_clip(
        clip_id="legacy-play",
        expression="focused",
        source_bytes=_png_sprite_sheet(),
        filename="focused.png",
        content_type="image/png",
        fps=10,
        grid_rows=3,
        grid_cols=10,
    )
    calls: list[str] = []

    async def fake_proxy_post_bytes(path, *_args, **_kwargs):
        calls.append(path)
        return 200, {"ok": True, "c6_confirmed": True}, "application/json"

    async def fake_proxy_post(path, *_args, **_kwargs):
        calls.append(path)
        return 200, {"ok": True, "c6_confirmed": True}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_post_bytes", fake_proxy_post_bytes)
    monkeypatch.setattr(gateway.server.esp32, "proxy_post", fake_proxy_post)

    with TestClient(gateway.app) as client:
        response = client.post("/api/expressions/play", json={"eye_clip_id": "legacy-play"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == ["/device/expression-clips/upload", "/device/expressions/play"]


def test_expression_clip_sync_surfaces_device_error(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)
    create_expression_clip(
        clip_id="focused",
        expression="focused",
        source_bytes=_png_sprite_sheet(),
        filename="focused.png",
        content_type="image/png",
        fps=10,
        grid_rows=3,
        grid_cols=10,
    )

    async def fake_proxy_post_bytes(*_args, **_kwargs):
        return 400, {"ok": False, "error": "manifest open failed"}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_post_bytes", fake_proxy_post_bytes)

    with TestClient(gateway.app) as client:
        response = client.post("/api/device/expression-clips/sync", json={"clip_id": "focused"})

    assert response.status_code == 400
    assert response.json()["error"] == "device sync failed: manifest open failed"
