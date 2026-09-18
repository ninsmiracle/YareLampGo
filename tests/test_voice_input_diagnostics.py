import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from livekit import rtc

from lampgo.voice.input_diagnostics import instrument_stt_node, log_vad_metrics


@pytest.mark.asyncio
async def test_input_meter_preserves_frames_and_reports_stall_then_cleans_up(caplog):
    caplog.set_level(logging.INFO)
    frame = rtc.AudioFrame(b"\x00\x40" * 160, 16000, 1, 160)
    queue = asyncio.Queue()
    queue.put_nowait(frame)
    received = []
    closed = asyncio.Event()

    async def audio():
        while True:
            yield await queue.get()

    async def original(agent, source, settings):
        try:
            async for incoming in source:
                received.append(incoming)
                yield SimpleNamespace(type="speech")
        finally:
            closed.set()

    source = audio()
    stream = instrument_stt_node(original, interval=0.01)(None, source, None)
    await anext(stream)
    assert received == [frame]
    assert received[0] is frame
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.035)
    assert "frames=1 samples=160" in caplog.text
    assert "rms_max=0.5000 peak_max=0.5000" in caplog.text
    assert "rms_max=0.0000 peak_max=0.0000" in caplog.text
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert closed.is_set()
    assert not any(t.get_name() == "lampgo-agent-input-diagnostics" for t in asyncio.all_tasks())
    await source.aclose()


@pytest.mark.asyncio
async def test_input_meter_propagates_error_without_leaking_timer():
    async def original(*args):
        raise RuntimeError("STT stream failed")
        yield  # async generator contract

    async def audio():
        yield None

    stream = instrument_stt_node(original)(None, audio(), None)
    with pytest.raises(RuntimeError, match="STT stream failed"):
        await anext(stream)
    assert not any(t.get_name() == "lampgo-agent-input-diagnostics" for t in asyncio.all_tasks())


def test_vad_metrics_only_logs_detection_counts(caplog):
    caplog.set_level(logging.INFO)
    log_vad_metrics(SimpleNamespace(metrics=SimpleNamespace(type="tts_metrics")))
    assert not caplog.records
    log_vad_metrics(SimpleNamespace(metrics=SimpleNamespace(
        type="vad_metrics", inference_count=150, inference_duration_total=0.05, idle_time=7,
    )))
    assert "inference_count=150" in caplog.text


@pytest.mark.asyncio
async def test_browser_diagnostics_accepts_only_bounded_metrics(monkeypatch):
    from lampgo.web import gateway

    log = Mock()
    monkeypatch.setattr(gateway, "logger", log)
    instance = object.__new__(gateway.WebGateway)
    await instance._handle_ws_message(None, {
        "type": "voice_input_diagnostics", "frames": 100, "rms": 0.2,
        "bytes_sent": float("inf"), "peak": float("nan"), "packets_sent": 10 ** 1000,
        "ctx": "running", "audio": "never log", "token": "never log",
        "client_call_id": "x" * 500,
    })
    log.info.assert_called_once_with(
        "web.voice_input_diagnostics", frames=100, rms=0.2, ctx="running", client_call_id="x" * 80,
    )
