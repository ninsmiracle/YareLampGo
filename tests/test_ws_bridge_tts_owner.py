from __future__ import annotations

from typing import Any

import pytest
from starlette.websockets import WebSocketState

from lampgo.core.events import EventBus
from lampgo.web.ws_bridge import WsBridge


class FakeWebSocket:
    def __init__(self) -> None:
        self.client_state = WebSocketState.CONNECTED
        self.json_messages: list[dict[str, Any]] = []
        self.bytes_messages: list[bytes] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.json_messages.append(msg)

    async def send_bytes(self, data: bytes) -> None:
        self.bytes_messages.append(data)


@pytest.mark.asyncio
async def test_tts_audio_broadcasts_to_single_claimed_client() -> None:
    bridge = WsBridge(EventBus())
    first = FakeWebSocket()
    second = FakeWebSocket()

    await bridge.add_client(first)  # type: ignore[arg-type]
    await bridge.add_client(second)  # type: ignore[arg-type]

    msg = {"type": "event", "event": "TtsAudio", "data": {"audio": "abc"}}
    await bridge.broadcast(msg)

    assert len(first.json_messages) == 1
    assert second.json_messages == []

    await bridge.claim_tts_client(second, active=True)  # type: ignore[arg-type]
    await bridge.broadcast(msg)

    assert len(first.json_messages) == 1
    assert len(second.json_messages) == 1


@pytest.mark.asyncio
async def test_non_tts_events_still_broadcast_to_every_client() -> None:
    bridge = WsBridge(EventBus())
    first = FakeWebSocket()
    second = FakeWebSocket()

    await bridge.add_client(first)  # type: ignore[arg-type]
    await bridge.add_client(second)  # type: ignore[arg-type]

    msg = {"type": "event", "event": "ChatMessage", "data": {"content": "hi"}}
    await bridge.broadcast(msg)

    assert len(first.json_messages) == 1
    assert len(second.json_messages) == 1
