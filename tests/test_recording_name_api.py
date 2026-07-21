from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.recordings import (
    list_recording_catalog,
    normalize_recording_name,
    read_recording_metadata,
    recording_override_path,
)
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


def test_recording_update_can_create_an_action_specific_expression_preset(monkeypatch, tmp_path: Path) -> None:
    gateway = _gateway(monkeypatch, tmp_path)
    csv_path = tmp_path / "recordings" / "user" / "开心挥手.csv"
    csv_path.parent.mkdir(parents=True)
    csv_text = "timestamp,base_yaw.pos\n0.0,0\n"
    csv_path.write_text(csv_text, encoding="utf-8")
    created: list[dict] = []

    def fake_save_expression_preset(payload: dict) -> dict:
        created.append(payload)
        return {"preset_id": payload["preset_id"]}

    monkeypatch.setattr("lampgo.web.gateway.save_expression_preset", fake_save_expression_preset)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/recordings/update",
            json={
                "name": "开心挥手",
                "description": "抬头挥手回应问候",
                "eye_clip_id": "happy_eyes",
                "led_effect_id": "heart",
            },
        )

    assert response.status_code == 200
    preset_id = response.json()["result"]["expression_preset"]
    assert preset_id.startswith("motion_")
    assert created == [
        {
            "preset_id": preset_id,
            "label": "动作：开心挥手",
            "description": "录制动作专用组合",
            "eye_clip_id": "happy_eyes",
            "led_effect_id": "heart",
            "playback": "loop",
            "duration_ms": 3000,
        }
    ]
    assert csv_path.read_text(encoding="utf-8") == csv_text
    assert read_recording_metadata(csv_path)["expression_preset"] == preset_id


def test_recording_update_overrides_builtin_metadata_without_changing_factory_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    gateway = _gateway(monkeypatch, tmp_path)
    csv_path = tmp_path / "recordings" / "开心挥手.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("timestamp,base_yaw.pos\n0.0,0\n", encoding="utf-8")
    factory_metadata = "expression=smiley\nprompt=出厂挥手动作\n"
    csv_path.with_suffix(".txt").write_text(factory_metadata, encoding="utf-8")

    def fake_save_expression_preset(payload: dict) -> dict:
        return {"preset_id": payload["preset_id"]}

    monkeypatch.setattr("lampgo.web.gateway.save_expression_preset", fake_save_expression_preset)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/recordings/update",
            json={
                "name": "开心挥手",
                "description": "开心地挥手回应用户",
                "eye_clip_id": "happy_eyes",
                "led_effect_id": "heart",
            },
        )

    assert response.status_code == 200
    preset_id = response.json()["result"]["expression_preset"]
    assert response.json()["result"]["source"] == "builtin"
    assert csv_path.with_suffix(".txt").read_text(encoding="utf-8") == factory_metadata
    override = recording_override_path(tmp_path / "recordings", "开心挥手")
    assert read_recording_metadata(override.with_suffix(".csv")) == {
        "description": "开心地挥手回应用户",
        "expression": "",
        "expression_preset": preset_id,
    }
    assert list_recording_catalog(tmp_path / "recordings")[0]["expression_preset"] == preset_id
