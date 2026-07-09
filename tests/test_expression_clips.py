from __future__ import annotations

import base64
from pathlib import Path

import numpy as np
import pytest
from starlette.testclient import TestClient

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.expression_clips import create_expression_clip, list_expression_clips
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


def test_expression_clip_api_upload_and_sync(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)
    sent: list[tuple[str, dict[str, object]]] = []

    async def fake_proxy_post(path: str, payload: dict[str, object]):
        sent.append((path, payload))
        return 200, {"ok": True}, "application/json"

    monkeypatch.setattr(gateway.server.esp32, "proxy_post", fake_proxy_post)

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
        assert sync.json()["result"]["sent_chunks"] == len(sent)

    assert sent[0][0] == "/device/expression-clips/sync"
    assert sent[0][1]["action"] == "begin"
    assert sent[0][1]["led_effect"] == "focused"
    assert not any(payload["action"] == "chunk" and payload["target"] == "led" for _path, payload in sent)
    assert any(payload["action"] == "chunk" and payload["target"] == "lcd" for _path, payload in sent)
    assert sent[-1][1]["action"] == "commit"
