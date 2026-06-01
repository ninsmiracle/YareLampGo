from __future__ import annotations

import json

from starlette.testclient import TestClient

from lampgo import personastore, sessionstore
from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


def _gateway(monkeypatch, tmp_path) -> WebGateway:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    return WebGateway(server)


def test_remote_api_requires_gateway_token(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app, client=("203.0.113.10", 50000)) as client:
        response = client.get("/api/config")

    assert response.status_code == 401
    assert response.json()["error"] == "authentication required"


def test_remote_api_accepts_bearer_token(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    token = personastore.get_or_create_plugin_token()

    with TestClient(gateway.app, client=("203.0.113.10", 50000)) as client:
        response = client.get("/api/config", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"


def test_loopback_ui_bootstrap_cookie_allows_api(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app, client=("127.0.0.1", 50000)) as client:
        index = client.get("/")
        response = client.get("/api/config")

    assert index.status_code == 200
    assert "lampgo_auth" in index.headers.get("set-cookie", "")
    assert response.status_code == 200


def test_cross_site_origin_is_rejected(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    token = personastore.get_or_create_plugin_token()

    with TestClient(gateway.app, client=("127.0.0.1", 50000)) as client:
        response = client.get(
            "/api/config",
            headers={"Origin": "https://evil.example", "Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_esp32_probe_rejects_non_device_targets(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/device/probe",
            json={"base_url": "http://169.254.169.254", "path": "/scan", "method": "GET"},
        )

    assert response.status_code == 400
    assert "ESP32" in response.json()["error"]


def test_llm_config_rejects_custom_private_base_url(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    monkeypatch.setattr(gateway.server, "reload_llm_client", lambda: None)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/config/llm",
            json={
                "validate": False,
                "provider": "custom",
                "api_base": "http://127.0.0.1:9999/v1",
                "model": "custom-model",
                "fast_model": "custom-model",
            },
        )

    assert response.status_code == 400
    assert "内网" in response.json()["error"] or "https" in response.json()["error"]


def test_memory_daily_rejects_path_traversal(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app) as client:
        response = client.get("/api/memory/daily?date=../../etc/passwd")

    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_config_response_masks_voice_credentials(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    gateway.server.config.voice.livekit_api_secret = "super-secret-value"

    with TestClient(gateway.app) as client:
        response = client.get("/api/config")

    value = response.json()["result"]["sections"]["voice"]["voice.livekit_api_secret"]["value"]
    assert value != "super-secret-value"
    assert value.startswith("supe")


def test_sessionstore_drops_unsafe_activity_html(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    snapshot = {
        "active_session_id": "s_xss",
        "sessions": [
            {
                "id": "s_xss",
                "title": "xss",
                "messages": [
                    {
                        "role": "assistant",
                        "text": "ok",
                        "meta": {"activity_html": '<div class="steps activity-log"><img src=x onerror=alert(1)></div>'},
                    }
                ],
            }
        ],
    }

    stored = sessionstore.save_snapshot(snapshot)

    assert "activity_html" not in stored["sessions"][0]["messages"][0].get("meta", {})
    raw = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert "onerror" not in json.dumps(raw)
