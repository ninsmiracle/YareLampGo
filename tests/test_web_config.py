"""End-to-end smoke tests for the generic web config endpoints (PR-C)."""
from __future__ import annotations

from starlette.testclient import TestClient

from lampgo import personastore
from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


def _isolate_env(monkeypatch, tmp_path):
    """Keep load_config_with_provenance from touching the dev's project tree.

    We point LAMPGO_HOME at tmp_path (isolates user-config + credentials),
    chdir into a scratch dir with a pyproject.toml but no .env (isolates the
    .env and project_root search), and stub load_dotenv for belt-and-braces.
    """
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    # Drop any LAMPGO_* env vars left over from the dev shell so provenance
    # tests aren't polluted (each individual test can add the ones it needs).
    for key in list(__import__("os").environ):
        if key.startswith("LAMPGO_") and key != "LAMPGO_HOME":
            monkeypatch.delenv(key, raising=False)
    scratch = tmp_path / "cwd"
    scratch.mkdir()
    (scratch / "pyproject.toml").write_text("[project]\nname='scratch'\n", encoding="utf-8")
    monkeypatch.chdir(scratch)
    monkeypatch.setattr("lampgo.core.config.load_dotenv", lambda *_a, **_kw: None)


def _make_gateway(monkeypatch, tmp_path) -> WebGateway:
    _isolate_env(monkeypatch, tmp_path)
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    return WebGateway(server)


def test_api_config_get_returns_sections_and_provenance(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app) as client:
        response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    result = body["result"]
    assert set(result["sections"].keys()) == {"device", "voice", "motion", "safety", "web", "device_esp32"}
    device = result["sections"]["device"]
    assert "device.motor_port" in device
    cell = device["device.motor_port"]
    assert cell["value"] == "/dev/null"
    # No user overrides yet → motor_port source is whatever the CLI gave it
    # (here: server was constructed in-process, so provenance is "default").
    assert cell["source"] in {"default", "cli", "user_config"}
    assert "cold_restart_fields" in result
    assert "device.motor_port" not in result["cold_restart_fields"]
    assert "device.led_port" not in device


def test_api_config_post_motion_writes_overrides_and_hot_applies(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)

    payload = {
        "motion.default_max_velocity": 3.5,
        "motion.default_style": "snappy",
        "motion.idle_sway_enabled": False,
        "motion.idle_sway_idle_after_s": 42.0,
        "motion.idle_sway_interval_s": 13.0,
    }

    with TestClient(gateway.app) as client:
        response = client.post("/api/config/motion", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    result = body["result"]
    assert set(result["saved"]) == set(payload.keys())
    # motion fields should NOT require a restart.
    assert result["needs_restart"] == []

    # Config object mutated in place.
    server = gateway.server
    assert server.config.motion.default_max_velocity == 3.5
    assert server.config.motion.default_style == "snappy"
    assert server.config.motion.idle_sway_enabled is False
    assert server.config.motion.idle_sway_idle_after_s == 42.0
    assert server.config.motion.idle_sway_interval_s == 13.0

    # ~/.lampgo/config.toml persisted.
    overrides = personastore.get_overrides_toml()
    assert overrides["motion"]["default_max_velocity"] == 3.5
    assert overrides["motion"]["default_style"] == "snappy"
    assert overrides["motion"]["idle_sway_enabled"] is False
    assert overrides["motion"]["idle_sway_idle_after_s"] == 42.0
    assert overrides["motion"]["idle_sway_interval_s"] == 13.0

    # Second GET reflects the new values with source=user_config.
    with TestClient(gateway.app) as client:
        refresh = client.get("/api/config").json()
    motion = refresh["result"]["sections"]["motion"]
    assert motion["motion.default_max_velocity"]["value"] == 3.5
    assert motion["motion.default_max_velocity"]["source"] == "user_config"


def test_api_config_post_device_hot_reloads_motor_port(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)
    gateway.server._started = True
    reload_called = False

    async def fake_reload_motor_runtime():
        nonlocal reload_called
        reload_called = True
        return {"ok": True, "connected": True, "mode": "hardware", "port": "/dev/tty.usbmodem9999"}

    monkeypatch.setattr(gateway.server, "reload_motor_runtime", fake_reload_motor_runtime)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/config/device",
            json={"device.motor_port": "/dev/tty.usbmodem9999"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["result"]["needs_restart"] == []
    assert reload_called is True
    assert body["result"]["hot_reload"]["device.motor_port"]["connected"] is True
    assert gateway.server.config.device.motor_port == "/dev/tty.usbmodem9999"
    overrides = personastore.get_overrides_toml()
    assert overrides["device"]["motor_port"] == "/dev/tty.usbmodem9999"


def test_api_config_post_device_bare_field_name_is_accepted(monkeypatch, tmp_path):
    """UI may POST either dotted keys or bare field names; both must work."""
    gateway = _make_gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/config/device",
            json={"motor_port": "/dev/tty.bare", "lamp_id": "AL42"},
        )

    assert response.status_code == 200
    assert gateway.server.config.device.motor_port == "/dev/tty.bare"
    assert gateway.server.config.device.lamp_id == "AL42"


def test_api_config_post_rejects_unknown_fields(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/config/device",
            json={"llm.api_key": "blocked-test-key"},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False


def test_api_config_restart_returns_hint(monkeypatch, tmp_path):
    gateway = _make_gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app) as client:
        response = client.post("/api/config/restart")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["result"]["restarted"] is False
    assert "Ctrl+C" in body["result"]["hint"]


def test_api_config_env_override_reported_in_provenance(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LAMPGO_SAFETY_MAX_VELOCITY", "4.2")
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    gateway = WebGateway(server)

    with TestClient(gateway.app) as client:
        body = client.get("/api/config").json()

    safety = body["result"]["sections"]["safety"]
    assert safety["safety.max_velocity"]["source"] == "env"
    assert "safety.max_velocity" in body["result"]["env_overrides"]
