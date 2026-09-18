import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lampgo.core.config import LampgoConfig
from lampgo.web import gateway as gateway_module


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stable", "interruptible", "esp32_aec"])
async def test_token_supplies_effective_voice_config_without_settings_visit(monkeypatch, mode):
    config = LampgoConfig()
    config.voice.call_mode = mode
    config.voice.echo_gate_hangover_ms = 250
    config.voice.echo_text_filter_enabled = False
    # Exercise token issuance without a device, an agent process or cloud room.
    gateway = object.__new__(gateway_module.WebGateway)
    from unittest.mock import Mock
    gateway.server = SimpleNamespace(config=config, ensure_agent_sdk_ready=AsyncMock(return_value=(True, "")),
                                     begin_voice_display=Mock())
    gateway._livekit_active_rooms = {}
    gateway._close_existing_livekit_rooms = AsyncMock()

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"token": "test-token", "roomName": json["room_name"], "serverUrl": "wss://example.test"},
            )

    monkeypatch.setattr(gateway_module.httpx_module, "AsyncClient", Client)
    response = await gateway._issue_livekit_token_locked(
        user_identity="test-user", voice_agent="lampgo-jarvis",
        client_call_id="test-call", reason="manual", audio_source="esp32",
    )
    body = json.loads(response.body)
    gateway.server.begin_voice_display.assert_called_once()
    assert body["result"]["token"] == "test-token"
    assert body["voice_config"] == {
        "call_mode": mode, "echo_gate_hangover_ms": 250, "echo_text_filter_enabled": False,
    }


@pytest.mark.asyncio
async def test_session_state_diagnostics_cover_subsequent_turns(monkeypatch, capsys):
    from livekit.agents.voice.agent_session import AgentSession
    from livekit.agents.voice.events import AgentStateChangedEvent, UserStateChangedEvent
    from lampgo.voice.agent_sdk import _SITECUSTOMIZE_CODE
    import os
    import sys

    compile(_SITECUSTOMIZE_CODE, "sitecustomize.py", "exec")
    original = AgentSession.__init__
    monkeypatch.setattr(AgentSession, "__init__", original)  # restore after executing patch
    start = _SITECUSTOMIZE_CODE.index("try:\n    from livekit.agents.voice.agent_session")
    end = _SITECUSTOMIZE_CODE.index("\ntry:", start + 1)
    exec(_SITECUSTOMIZE_CODE[start:end], {"os": os, "sys": sys})
    session = AgentSession()
    assert session._aec_warmup_remaining == 0.0
    assert AgentSession(aec_warmup_duration=2.0)._aec_warmup_remaining == 2.0
    for _ in range(2):
        session.emit("user_state_changed", UserStateChangedEvent(old_state="listening", new_state="speaking"))
        session.emit("agent_state_changed", AgentStateChangedEvent(old_state="speaking", new_state="listening"))
    output = capsys.readouterr().err
    assert output.count("event=user_state_changed old=listening new=speaking") == 2
    assert output.count("event=agent_state_changed old=speaking new=listening") == 2


@pytest.mark.asyncio
async def test_wireless_vad_keeps_quiet_prefix_without_delaying_detection(monkeypatch):
    import os
    import sys
    import numpy as np
    from livekit import rtc
    from livekit.plugins import silero
    from livekit.plugins.silero import onnx_model
    from livekit.agents.vad import VADEventType
    from lampgo.voice.agent_sdk import _SITECUSTOMIZE_CODE

    # A deterministic detector misses the soft opening, then recognizes the
    # louder second half. Exercise the real streaming segment assembly, not
    # an ASR mock that already contains the desired complete sentence.
    monkeypatch.setattr(onnx_model.OnnxModel, "__call__",
                        lambda self, x: 0.99 if np.max(x) > 0.01 else 0.0)
    original_load = silero.VAD.__dict__["load"]
    monkeypatch.setattr(silero.VAD, "load", original_load)
    start = _SITECUSTOMIZE_CODE.index("try:\n    from livekit.plugins import silero as _lampgo_silero")
    end = _SITECUSTOMIZE_CODE.index("\ntry:", start + 1)
    exec(_SITECUSTOMIZE_CODE[start:end], {"os": os, "sys": sys})

    pcm = np.concatenate([
        np.zeros(16000, dtype=np.int16),
        np.full(16000, 20, dtype=np.int16),  # soft first second
        np.full(16000, 2000, dtype=np.int16),
        np.zeros(16000, dtype=np.int16),
    ])

    async def segment(**kwargs):
        vad = silero.VAD.load(**kwargs)
        stream = vad.stream()
        try:
            for offset in range(0, len(pcm), 480):
                chunk = pcm[offset:offset + 480]
                stream.push_frame(rtc.AudioFrame(chunk.tobytes(), 16000, 1, len(chunk)))
            stream.end_input()
            events = [event async for event in stream if event.type != VADEventType.INFERENCE_DONE]
            assert len(events) == 2
            data = rtc.combine_audio_frames(events[1].frames).data
            return events[0].timestamp, sum(v == 20 for v in data)
        finally:
            await stream.aclose()

    previous_start, previous_prefix = await segment(prefix_padding_duration=0.5)
    new_start, new_prefix = await segment()
    assert new_start == previous_start  # look-back introduces no turn waiting time
    assert previous_prefix < 9600  # only about half of the soft first second
    assert new_prefix == 16000  # complete soft opening is included in the ASR WAV
