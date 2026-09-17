from __future__ import annotations

import asyncio
import hashlib
import hmac

import httpx

from lampgo.core.config import DeviceEsp32Config
from lampgo.device.audio_stream import build_ws_audio_url, build_ws_speaker_url, send_stream_auth
from lampgo.device.esp32 import Esp32Device, Esp32DeviceManager
from lampgo.device.p4_auth import AUTH_DOMAIN, build_p4_auth_fields, build_p4_auth_proof


def test_p4_proof_uses_only_a_derived_pairing_key() -> None:
    proof = build_p4_auth_proof(
        owner_id="lampgo-owner",
        pairing_secret="pairing-secret-for-test",
        purpose="ws:audio",
        nonce="0123456789abcdef",
    )
    key = hashlib.sha256(b"pairing-secret-for-test").hexdigest().encode("ascii")
    expected = hmac.new(
        key,
        f"{AUTH_DOMAIN}\nws:audio\nlampgo-owner\n0123456789abcdef".encode(),
        hashlib.sha256,
    ).hexdigest()

    assert proof == expected
    assert "pairing-secret-for-test" not in proof


def test_p4_auth_fields_do_not_contain_the_pairing_secret() -> None:
    fields = build_p4_auth_fields(
        owner_id="lampgo-owner",
        pairing_secret="pairing-secret-for-test",
        purpose="asset:POST:/device/expression-clips/upload:chunk",
        nonce="nonce",
    )

    assert fields["owner_id"] == "lampgo-owner"
    assert fields["auth_purpose"].endswith(":chunk")
    assert "pairing_secret" not in fields
    assert "pairing-secret-for-test" not in fields.values()


def test_p4_audio_url_does_not_embed_replayable_pairing_credentials() -> None:
    class Device:
        ip = "192.0.2.4"
        host = "lampgo-p4.local"
        port = 80

    class Manager:
        def _pick_active(self):
            return Device()

    assert build_ws_audio_url(Manager()) == "ws://192.0.2.4:81/ws/audio"
    assert build_ws_speaker_url(Manager()) == "ws://192.0.2.4:81/ws/speaker"


def test_p4_audio_auth_uses_the_device_manager_not_a_transport_url() -> None:
    class WebSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, value: str) -> None:
            self.sent.append(value)

        async def recv(self) -> str:
            return '{"type":"challenge","purpose":"ws:audio","nonce":"test-nonce"}'

    class Manager:
        owner_id = "lampgo-owner"
        pairing_secret = "pairing-secret-for-test"

    ws = WebSocket()
    asyncio.run(send_stream_auth(ws, Manager()))

    assert '"type":"auth_init"' in ws.sent[0]
    assert '"purpose":"ws:audio"' in ws.sent[0]
    assert '"owner_id":"lampgo-owner"' in ws.sent[1]
    assert "pairing-secret-for-test" not in ws.sent[1]


def test_p4_control_post_replaces_pairing_secret_with_single_use_proof(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo-home"))
    manager = Esp32DeviceManager(DeviceEsp32Config(enabled=True))
    manager._owner_id = "lampgo-owner"
    manager._pairing_secret = "pairing-secret-for-test"
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/device/auth/challenge":
            assert request.url.params["purpose"] == "http:POST:/device/led"
            return httpx.Response(200, json={"ok": True, "nonce": "single-use-nonce"}, request=request)
        seen["body"] = request.json() if hasattr(request, "json") else None
        # httpx.Request deliberately has no .json() helper.
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True}, request=request)

    async def run() -> tuple[int, dict[str, object], str]:
        manager._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await manager._post_to_device(
                Esp32Device("p4", "lampgo-p4.local", extras={"platform": "esp32-p4"}),
                "/device/led",
                {"enabled": True, "owner_id": "lampgo-owner", "pairing_secret": "pairing-secret-for-test"},
            )
        finally:
            await manager._http.aclose()

    status, body, _ = asyncio.run(run())

    assert status == 200 and body["ok"] is True
    sent = seen["body"]
    assert isinstance(sent, dict)
    assert sent["auth_purpose"] == "http:POST:/device/led"
    assert "pairing_secret" not in sent


def test_asset_timeout_reports_phase_and_acknowledged_progress(monkeypatch, tmp_path):
    from lampgo.device.esp32 import P4_ASSET_CHUNK_BYTES
    monkeypatch.setenv('LAMPGO_HOME', str(tmp_path))
    manager = Esp32DeviceManager(DeviceEsp32Config(enabled=True))
    dev = Esp32Device('p4', 'lampgo-p4.local', extras={'platform': 'esp32-p4'})
    monkeypatch.setattr(manager, '_pick_active', lambda: dev)
    chunks = 0

    async def handler(request):
        nonlocal chunks
        if request.url.path == '/device/auth/challenge':
            return httpx.Response(200, json={'nonce': 'test-nonce'})
        if request.headers.get('X-Lampgo-Upload-Phase') == 'chunk':
            chunks += 1
            if chunks == 2:
                raise httpx.ReadTimeout('', request=request)
        return httpx.Response(200, json={'ok': True})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            manager._http = client
            return await manager.proxy_post_bytes('/device/expression-clips/upload',
                                                  b'x' * (P4_ASSET_CHUNK_BYTES + 1),
                                                  params={'clip_id': 'legacy'})

    status, body, _ = asyncio.run(run())
    assert status == 502
    assert body['error'] == 'proxy_failed: ReadTimeout during chunk'
    assert body['sent_bytes'] == P4_ASSET_CHUNK_BYTES
    assert body['total_bytes'] == P4_ASSET_CHUNK_BYTES + 1
    assert manager._active_transfers == 0
