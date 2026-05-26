from __future__ import annotations

from starlette.testclient import TestClient

from lampgo.core.config import DeviceConfig, DeviceEsp32Config, LampgoConfig
from lampgo.device.esp32 import Esp32Device, Esp32DeviceManager
from lampgo.server import LampgoServer
from lampgo.web.gateway import WebGateway


def _device(host: str, *, owner_id: str = "", paired: bool = False, seen: float = 1.0) -> Esp32Device:
    return Esp32Device(
        device_id=host,
        host=host,
        hostname=host,
        last_seen=seen,
        last_health_ok=True,
        last_health_ok_at=seen,
        extras={
            "pairing_supported": True,
            "paired": paired,
            "paired_owner_id": owner_id,
            "paired_owner_label": f"{owner_id}-label" if owner_id else "",
        },
    )


def test_foreign_paired_devices_are_hidden_and_not_active(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    manager = Esp32DeviceManager(DeviceEsp32Config(enabled=True))

    self_device = _device("self.local", owner_id=manager.owner_id, paired=True, seen=1.0)
    free_device = _device("free.local", paired=False, seen=2.0)
    foreign_device = _device("foreign.local", owner_id="other-owner", paired=True, seen=99.0)
    manager._devices = {
        "self": self_device,
        "free": free_device,
        "foreign": foreign_device,
    }

    status = manager.get_status()

    visible_hosts = {d["host"] for d in status["all_devices"]}
    assert visible_hosts == {"self.local", "free.local"}
    assert status["blocked_devices_count"] == 1
    assert status["device"]["host"] == "free.local"

    manager._devices = {"foreign": foreign_device}
    status = manager.get_status()
    assert status["configured"] is False
    assert status["device"] is None
    assert status["all_devices"] == []
    assert status["blocked_devices_count"] == 1


def test_preferred_fallback_keeps_pairing_probe_status(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    manager = Esp32DeviceManager(DeviceEsp32Config(enabled=True, preferred_host="lampgo-cam-0834.local"))

    fallback = manager._preferred_fallback_device()
    assert fallback is not None
    manager._merge_device_status(
        fallback,
        {
            "firmware": "lampgo-cam 0.2.0",
            "hostname": "lampgo-cam-0834",
            "ip": "192.168.31.228",
            "pairing_supported": True,
            "paired": True,
            "paired_owner_id": manager.owner_id,
            "paired_owner_label": manager.owner_label,
            "pairing_state": "paired",
        },
    )
    fallback.last_health_ok = True
    fallback.last_health_ok_at = 123.0
    manager._preferred_fallback_snapshot = fallback
    manager._preferred_health_ok = True
    manager._preferred_health_ok_at = 123.0

    status = manager.get_status()

    assert status["configured"] is True
    assert status["device"]["host"] == "lampgo-cam-0834.local"
    assert status["device"]["ip"] == "192.168.31.228"
    assert status["device"]["pairing_supported"] is True
    assert status["device"]["needs_firmware_update"] is False
    assert status["device"]["is_paired_to_self"] is True
    assert [d["host"] for d in status["all_devices"]] == ["lampgo-cam-0834.local"]


async def test_claim_owner_is_non_preemptive_and_sends_auth(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    manager = Esp32DeviceManager(DeviceEsp32Config(enabled=True))
    captured: dict[str, object] = {}

    async def fake_proxy_post(path: str, body: dict[str, object]):
        captured["path"] = path
        captured["body"] = body
        return 403, {"ok": False, "error": "pairing_mismatch"}, "application/json"

    monkeypatch.setattr(manager, "proxy_post", fake_proxy_post)

    ok = await manager.claim_owner(force=True, reason="test")

    assert ok is False
    assert captured["path"] == "/device/claim"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["owner_id"] == manager.owner_id
    assert body["owner_label"] == manager.owner_label
    assert body["pairing_secret"] == manager.pairing_secret
    assert body["reason"] == "test"
    assert "force" not in body


def test_probe_connect_injects_pairing_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = LampgoServer(
        LampgoConfig(
            device=DeviceConfig(motor_port="/dev/null"),
            device_esp32=DeviceEsp32Config(enabled=True),
        )
    )
    gateway = WebGateway(server)
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"ok":true}'

        def json(self):
            return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object]):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

        async def get(self, url: str):
            raise AssertionError("probe should POST /connect")

    monkeypatch.setattr("lampgo.web.gateway.httpx_module.AsyncClient", FakeAsyncClient)

    with TestClient(gateway.app) as client:
        response = client.post(
            "/api/device/probe",
            json={
                "base_url": "http://192.168.4.1",
                "path": "/connect",
                "method": "POST",
                "body": {"ssid": "test-wifi", "password": "secret"},
            },
        )

    assert response.status_code == 200
    assert captured["url"] == "http://192.168.4.1/connect"
    body = captured["json"]
    assert isinstance(body, dict)
    assert body["ssid"] == "test-wifi"
    assert body["password"] == "secret"
    assert body["owner_id"] == server.esp32.owner_id
    assert body["owner_label"] == server.esp32.owner_label
    assert body["pairing_secret"] == server.esp32.pairing_secret
