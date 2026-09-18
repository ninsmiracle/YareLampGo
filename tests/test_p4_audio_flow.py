"""Flow-control regressions: no implicit unbounded TCP audio queues on P4."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from lampgo.device.audio_stream import P4SpeakerStream, acknowledge_audio_frame, send_stream_auth


class Socket:
    def __init__(self, replies=()):
        self.sent = []
        self.replies = asyncio.Queue()
        for reply in replies:
            self.replies.put_nowait(reply)

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        return await self.replies.get()


@pytest.mark.parametrize("advertised,enabled", [(None, False), ("ack-v1", True), ("unknown", False)])
def test_flow_control_is_negotiated_not_assumed(advertised, enabled):
    async def run():
        challenge = {"type": "challenge", "purpose": "ws:audio", "nonce": "test"}
        if advertised:
            challenge["flow_control"] = advertised
        ws = Socket([json.dumps(challenge)])
        manager = SimpleNamespace(owner_id="owner", pairing_secret="test-secret")
        assert await send_stream_auth(ws, manager) is enabled
        auth = json.loads(ws.sent[1])
        assert (auth.get("flow_control") == "ack-v1") is enabled
        await acknowledge_audio_frame(ws, enabled)
        assert len(ws.sent) == (3 if enabled else 2)
        if enabled:
            assert ws.sent[-1] == "ack"

    asyncio.run(run())


def test_speaker_has_at_most_four_packets_in_flight_even_for_a_large_browser_burst():
    async def run():
        ws = Socket()
        pcm = b"\x01\x02" * 6000
        task = asyncio.create_task(P4SpeakerStream(ws, True).send(pcm))
        try:
            for packet_count in (4, 5, 6):
                await asyncio.sleep(0.01)
                assert len(ws.sent) == packet_count
                assert not task.done()
                ws.replies.put_nowait("ack")
            await task
            assert b"".join(ws.sent) == pcm
            assert all(len(frame) <= 1920 for frame in ws.sent)
        finally:
            task.cancel()

    asyncio.run(run())


def test_speaker_bad_ack_aborts_without_sending_the_next_packet():
    async def run():
        ws = Socket(["invalid"])
        with pytest.raises(ConnectionError):
            await P4SpeakerStream(ws, True).send(b"\0" * 10000)
        assert len(ws.sent) == 4

    asyncio.run(run())


def test_speaker_old_firmware_does_not_wait_for_nonexistent_ack():
    async def run():
        ws = Socket()
        stream = P4SpeakerStream(ws, False)
        await asyncio.wait_for(stream.send(b"\0" * 4000), 0.2)
        assert list(map(len, ws.sent)) == [1920, 1920, 160]
        with pytest.raises(ValueError):
            await stream.send(b"\0")

    asyncio.run(run())


def test_speaker_no_ack_is_bounded_and_cancellable():
    async def run():
        ws = Socket()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(P4SpeakerStream(ws, True).send(b"\0" * 10000), 0.05)
        assert len(ws.sent) == 4

    asyncio.run(run())


def test_speaker_credit_limit_survives_multiple_send_calls():
    async def run():
        ws = Socket()
        stream = P4SpeakerStream(ws, True)
        for _ in range(4):
            await stream.send(b"\0" * 640)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(stream.send(b"\0" * 640), 0.05)
        assert len(ws.sent) == 4

    asyncio.run(run())


def test_gateway_discards_socket_after_failed_authentication(monkeypatch):
    import websockets

    from lampgo.web.gateway import WebGateway

    class DeviceSocket(Socket):
        closed = False

        async def close(self):
            self.closed = True

    class Browser:
        closed = None

        async def accept(self):
            pass

        async def close(self, code):
            self.closed = code

    target = DeviceSocket(["not a challenge"])

    async def connect(*args, **kwargs):
        return target

    monkeypatch.setattr(websockets, "connect", connect)
    gateway = object.__new__(WebGateway)
    gateway._is_websocket_authorized = lambda ws: True
    gateway._esp32_capture_active = False
    gateway._esp32_speaker_clients = set()
    gateway.server = SimpleNamespace(
        esp32=SimpleNamespace(
            _pick_active=lambda: SimpleNamespace(ip="192.0.2.4", host="p4.local", port=80),
        )
    )
    browser = Browser()
    asyncio.run(gateway.ws_esp32_speaker(browser))
    assert target.closed
    assert browser.closed == 1011
    assert not gateway._esp32_speaker_clients


def test_speaker_silence_gate_preserves_quiet_voice_and_pauses_across_twenty_turns():
    from lampgo.device.audio_stream import SpeakerSilenceGate
    gate = SpeakerSilenceGate()
    silence = b'\0' * 640
    assert not gate.filter(silence)  # no idle downlink before the first reply
    for _ in range(20):
        quiet_voice = b'\x01\x00' * 320  # even one LSB must not be classified as silence
        assert gate.filter(quiet_voice) == quiet_voice
        assert gate.filter(silence) == silence  # short intra-sentence pause
        assert gate.filter(quiet_voice) == quiet_voice
        assert b''.join(gate.filter(silence) for _ in range(100)) == b'\0' * 6400
        assert not gate.filter(silence)
    assert gate.skipped_bytes > 1_000_000


@pytest.mark.asyncio
async def test_microphone_deadline_ignores_text_and_propagates_cancellation():
    from lampgo.device.audio_stream import Esp32MicrophoneStream
    ws = Socket(['heartbeat'] * 100)
    with pytest.raises(ConnectionError, match='no PCM'):
        await Esp32MicrophoneStream(ws, idle_timeout_s=0.02).receive()
    ws.replies.put_nowait(b'\0' * 960)  # microphone silence is valid PCM, never filtered
    assert await Esp32MicrophoneStream(ws).receive() == b'\0' * 960
    task = asyncio.create_task(Esp32MicrophoneStream(ws).receive())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_gateway_reconnects_open_but_silent_microphone_and_only_announces_real_pcm(monkeypatch):
    import websockets
    from unittest.mock import AsyncMock, Mock
    from lampgo.device import audio_stream
    from lampgo.web.gateway import WebGateway

    real_stream = audio_stream.Esp32MicrophoneStream
    monkeypatch.setattr(audio_stream, 'Esp32MicrophoneStream',
                        lambda ws: real_stream(ws, idle_timeout_s=0.02))
    monkeypatch.setattr(audio_stream, 'send_stream_auth', AsyncMock(return_value=True))
    sockets = [Socket(), Socket([b'\x01\x00' * 480])]
    closed = []
    connections = []

    class Connection:
        def __init__(self):
            self.index = len(connections)
            connections.append(self)
        async def __aenter__(self): return sockets[self.index]
        async def __aexit__(self, *args): closed.append(self.index)

    monkeypatch.setattr(websockets, 'connect', lambda *a, **kw: Connection())
    pcm_received = asyncio.Event()
    states = []
    class Browser:
        async def send_json(self, value): states.append(value['data'].get('state'))
        async def send_bytes(self, pcm):
            assert pcm == b'\x01\x00' * 480
            pcm_received.set()

    gateway = object.__new__(WebGateway)
    gateway._esp32_capture_active = False
    gateway._ensure_esp32_mic_stream_enabled = AsyncMock()
    gateway._log_esp32_audio_health = AsyncMock()
    gateway.server = SimpleNamespace(_wake_loop=None, esp32=SimpleNamespace(
        claim_owner=AsyncMock(return_value=True), mark_active_healthy=Mock(),
        _pick_active=lambda: SimpleNamespace(ip='192.0.2.4', host='p4.local', port=80),
    ))
    task = asyncio.create_task(gateway._relay_esp32_audio_to_browser(Browser()))
    try:
        await asyncio.wait_for(pcm_received.wait(), timeout=2)
        assert states[:5] == ['connecting', 'authenticated', 'recovering', 'authenticated', 'connected']
        assert closed == [0]
        gateway._log_esp32_audio_health.assert_awaited_once()
        assert len(connections) == 2
        await asyncio.sleep(0)  # let ACK finish
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    assert closed == [0, 1]


@pytest.mark.asyncio
async def test_real_speaker_proxy_suppresses_idle_network_and_flushes_last_partial_reply(monkeypatch):
    import websockets
    from starlette.websockets import WebSocketDisconnect
    from lampgo.device import p4_auth
    from lampgo.web.gateway import WebGateway

    class Device(Socket):
        closed = False
        async def send(self, pcm):
            self.sent.append(pcm)
            self.replies.put_nowait('ack')
        async def close(self): self.closed = True

    device = Device()
    async def connect(*args, **kwargs): return device
    async def auth(*args, **kwargs): return True
    monkeypatch.setattr(websockets, 'connect', connect)
    monkeypatch.setattr(p4_auth, 'authenticate_p4_websocket', auth)

    voice = b'\x01\x00' * 320
    silence = b'\0' * 640
    all_input = [silence] * 50 + ([voice] * 5 + [silence] * 100) * 20
    expected = (voice * 5 + silence * 10) * 20
    browser_queue = asyncio.Queue()
    for pcm in all_input + [voice[:64]]: browser_queue.put_nowait(pcm)

    class Browser:
        async def accept(self): pass
        async def receive_bytes(self):
            msg = await browser_queue.get()
            if msg is None: raise WebSocketDisconnect()
            return msg
        async def close(self, code): pass

    gateway = object.__new__(WebGateway)
    gateway._is_websocket_authorized = lambda ws: True
    gateway._esp32_capture_active = False
    gateway._esp32_speaker_clients = set()
    gateway.server = SimpleNamespace(esp32=SimpleNamespace(
        _pick_active=lambda: SimpleNamespace(ip='192.0.2.4', host='p4.local', port=80),
    ))
    task = asyncio.create_task(gateway.ws_esp32_speaker(Browser()))
    try:
        # The browser now pauses without sending another silence frame. The
        # partial last word still has to flush within the 60 ms batch budget.
        async with asyncio.timeout(2):
            while b''.join(device.sent) != expected + voice[:64]:
                await asyncio.sleep(0.01)
        assert all(len(pcm) <= 1920 for pcm in device.sent)
        sent_count = len(device.sent)
        await asyncio.sleep(0.1)
        assert len(device.sent) == sent_count
    finally:
        browser_queue.put_nowait(None)
        await task
    assert device.closed
    assert not gateway._esp32_speaker_clients
