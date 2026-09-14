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
