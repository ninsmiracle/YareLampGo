from __future__ import annotations

from starlette.testclient import TestClient

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


def test_llm_config_get_normalizes_legacy_provider_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    server.config.llm.provider = "xiaomi"
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        response = client.get("/api/config/llm")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"]["provider"] == "mimo"


def test_llm_config_post_persists_canonical_provider_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    monkeypatch.setattr(server, "reload_llm_client", lambda: None)
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/config/llm",
            json={
                "validate": False,
                "provider": "xiaomi",
                "api_base": "",
                "model": "mimo-v2-omni",
                "fast_model": "mimo-v2-omni",
                "message_type": "openai",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"]["provider"] == "mimo"
    assert server.config.llm.provider == "mimo"
    config_text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'provider = "mimo"' in config_text
    assert 'provider = "xiaomi"' not in config_text
