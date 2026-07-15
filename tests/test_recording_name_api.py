from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.recordings import normalize_recording_name
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


def _gateway(monkeypatch, tmp_path: Path) -> WebGateway:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo-home"))
    config = LampgoConfig(device=DeviceConfig(motor_port="/dev/null"))
    config.recordings_dir = tmp_path / "recordings"
    return WebGateway(LampgoServer(config))


def test_recording_name_accepts_chinese_and_blocks_path_characters() -> None:
    assert normalize_recording_name(" 开心挥手 ") == "开心挥手"
    assert normalize_recording_name("hello_你好-2") == "hello_你好-2"
    assert normalize_recording_name("开心/挥手") == ""
    assert normalize_recording_name("开心 挥手") == ""


def test_recordings_api_stores_updates_and_deletes_chinese_names(monkeypatch, tmp_path: Path) -> None:
    gateway = _gateway(monkeypatch, tmp_path)
    name = "开心挥手"
    csv = "timestamp,base_yaw.pos\n0.0,0\n"

    with TestClient(gateway.app) as client:
        saved = client.post("/api/recordings/save", json={"name": name, "csv": csv, "description": "打招呼"})
        assert saved.status_code == 200
        assert saved.json()["result"]["name"] == name

        updated = client.post("/api/recordings/update", json={"name": name, "description": "热情地打招呼"})
        assert updated.status_code == 200
        assert updated.json()["result"]["recordings"][0]["name"] == name

        deleted = client.post("/api/recordings/delete", json={"name": name})
        assert deleted.status_code == 200
        assert deleted.json()["result"]["name"] == name

    assert not (tmp_path / "recordings" / "user" / f"{name}.csv").exists()


def test_recordings_api_rejects_path_like_names(monkeypatch, tmp_path: Path) -> None:
    gateway = _gateway(monkeypatch, tmp_path)
    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/recordings/save",
            json={"name": "../开心", "csv": "timestamp,base_yaw.pos\n0.0,0\n"},
        )
    assert response.status_code == 400
    assert "中文" in response.json()["error"]
