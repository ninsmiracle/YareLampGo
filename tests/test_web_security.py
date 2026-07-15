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
    token = personastore.get_or_create_local_api_token()

    with TestClient(gateway.app, client=("203.0.113.10", 50000)) as client:
        response = client.get("/api/config", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"


def test_local_api_token_is_automatic_and_migrates_legacy_key(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    personastore.set_credentials({"plugin_token": "legacy-local-token"})

    token = personastore.get_or_create_local_api_token()
    credentials = personastore.get_credentials()

    assert token == "legacy-local-token"
    assert credentials["local_api_token"] == token
    assert "plugin_token" not in credentials


def test_local_llm_compat_accepts_livekit_agent_token(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app, client=("127.0.0.1", 50000)) as client:
        rejected = client.post("/v1/chat/completions", json={})
        accepted = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer lampgo-local"},
            json={},
        )

    assert rejected.status_code == 401
    assert accepted.status_code == 400
    assert "no user message found" in accepted.text


def test_remote_llm_compat_rejects_livekit_agent_token(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app, client=("203.0.113.10", 50000)) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer lampgo-local"},
            json={},
        )

    assert response.status_code == 401
    assert response.json()["error"] == "authentication required"


def test_loopback_ui_bootstrap_cookie_allows_api(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)

    with TestClient(gateway.app, client=("127.0.0.1", 50000)) as client:
        index = client.get("/")
        response = client.get("/api/config")

    assert index.status_code == 200
    assert "lampgo_auth" in index.headers.get("set-cookie", "")
    assert response.status_code == 200


def test_cancel_agent_task_does_not_block_websocket_receive_loop(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)

    class BlockingAgent:
        async def cancel_task(self, _task_id: str) -> bool:
            await __import__("asyncio").Event().wait()
            return True

        def list_tasks(self):
            return [{"task_id": "still-responsive", "status": "running"}]

        def get_task(self, _task_id: str):
            return None

    gateway.server.agent = BlockingAgent()

    with TestClient(gateway.app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "cancel_agent_task", "task_id": "slow", "request_id": "cancel"})
        ws.send_json({"type": "agent_tasks", "request_id": "status"})
        response = ws.receive_json()

    assert response["request_id"] == "status"
    assert response["result"]["agent_tasks"][0]["task_id"] == "still-responsive"


def test_cross_site_origin_is_rejected(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    token = personastore.get_or_create_local_api_token()

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


def test_config_response_omits_livekit_frontend_fields(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    gateway.server.config.voice.livekit_url = "https://rtc.yhaox.top"
    gateway.server.config.voice.livekit_api_key = "root-key"
    gateway.server.config.voice.livekit_api_secret = "super-secret-value"
    gateway.server.config.voice.livekit_room = "secret-room"

    with TestClient(gateway.app) as client:
        response = client.get("/api/config")

    voice_section = response.json()["result"]["sections"]["voice"]
    assert "voice.livekit_url" not in voice_section
    assert "voice.livekit_api_key" not in voice_section
    assert "voice.livekit_api_secret" not in voice_section
    assert "voice.livekit_room" not in voice_section


def test_livekit_token_allows_only_one_active_call(monkeypatch, tmp_path):
    gateway = _gateway(monkeypatch, tmp_path)
    issued_payloads: list[dict[str, object]] = []

    async def fake_ready(*, timeout_s: float = 10.0) -> tuple[bool, str]:
        return True, ""

    gateway.server.ensure_agent_sdk_ready = fake_ready  # type: ignore[method-assign]

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "token": "token",
                "serverUrl": "wss://rtc.yhaox.top",
                "roomName": self._payload["room_name"],
                "identity": self._payload["user_identity"],
                "role": self._payload["voice_agent"],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object]):
            issued_payloads.append(json)
            return FakeResponse(json)

    monkeypatch.setattr("lampgo.web.gateway.httpx_module.AsyncClient", FakeAsyncClient)

    with TestClient(gateway.app) as client:
        first = client.post(
            "/api/livekit/token",
            json={
                "room_name": "custom-room-a",
                "user_identity": "user-a",
                "voice_agent": "lampgo-jarvis",
                "client_call_id": "call-a",
            },
        )
        first_room = first.json()["result"]["roomName"]
        gateway._livekit_token_gate_until = 0.0
        second = client.post(
            "/api/livekit/token",
            json={
                "room_name": "custom-room-b",
                "user_identity": "user-b",
                "voice_agent": "lampgo-jarvis",
                "client_call_id": "call-b",
            },
        )
        ended = client.post(
            "/api/livekit/room/end",
            json={"room_name": first_room, "reason": "test_end", "client_call_id": "call-a"},
        )
        gateway._livekit_token_gate_until = 0.0
        third = client.post(
            "/api/livekit/token",
            json={
                "user_identity": "user-b",
                "voice_agent": "lampgo-jarvis",
                "client_call_id": "call-b",
            },
        )

    assert first.status_code == 200
    assert str(first_room).startswith("lampgo-")
    assert second.status_code == 409
    assert second.json()["error"] == "another call is already active"
    assert ended.status_code == 200
    assert third.status_code == 200
    third_room = third.json()["result"]["roomName"]
    assert str(third_room).startswith("lampgo-")
    assert third_room != first_room
    assert [payload["room_name"] for payload in issued_payloads] == [first_room, third_room]


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
