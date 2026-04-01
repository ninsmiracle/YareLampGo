"""Tests for voice module components."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import struct

import pytest

from lampgo.voice.vad import EnergyVAD


# ── Audio helpers ──

def _make_silence(n_samples: int = 480) -> bytes:
    return struct.pack(f"<{n_samples}h", *([0] * n_samples))


def _make_speech(n_samples: int = 480, amplitude: int = 5000) -> bytes:
    samples = [int(amplitude * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n_samples)]
    return struct.pack(f"<{n_samples}h", *samples)


def _make_low_noise(n_samples: int = 480, amplitude: int = 30) -> bytes:
    samples = [int(amplitude * math.sin(2 * math.pi * 100 * i / 16000)) for i in range(n_samples)]
    return struct.pack(f"<{n_samples}h", *samples)


def _calibrate(vad: EnergyVAD, noise_func=_make_low_noise, frames: int = 50) -> None:
    """Feed calibration frames until the VAD is calibrated."""
    for _ in range(frames):
        vad.process_chunk(noise_func())
    assert vad.calibrated


# ── VAD tests ──

def test_vad_calibration():
    vad = EnergyVAD(calibration_frames=10)
    assert not vad.calibrated
    for _ in range(10):
        result = vad.process_chunk(_make_low_noise())
        assert result is False
    assert vad.calibrated
    assert vad.noise_floor > 0
    assert vad.threshold > vad.noise_floor


def test_vad_silence():
    vad = EnergyVAD(silence_frames=5, min_speech_frames=2, calibration_frames=10)
    _calibrate(vad, frames=10)
    for _ in range(20):
        vad.process_chunk(_make_silence())
    assert not vad.is_speaking


def test_vad_speech_detection():
    vad = EnergyVAD(silence_frames=5, min_speech_frames=2, calibration_frames=10)
    _calibrate(vad, frames=10)
    for _ in range(5):
        vad.process_chunk(_make_speech())
    assert vad.is_speaking


def test_vad_speech_then_silence():
    vad = EnergyVAD(silence_frames=3, min_speech_frames=2, calibration_frames=10)
    _calibrate(vad, frames=10)
    for _ in range(5):
        vad.process_chunk(_make_speech())
    assert vad.is_speaking
    for _ in range(10):
        vad.process_chunk(_make_silence())
    assert not vad.is_speaking


def test_vad_reset():
    vad = EnergyVAD(calibration_frames=10)
    _calibrate(vad, frames=10)
    for _ in range(10):
        vad.process_chunk(_make_speech())
    assert vad.is_speaking
    vad.reset()
    assert not vad.is_speaking


def test_vad_adapts_to_quiet_mic():
    vad = EnergyVAD(calibration_frames=10)
    _calibrate(vad, noise_func=lambda: _make_low_noise(amplitude=5), frames=10)
    assert vad.noise_floor < 10
    for _ in range(5):
        vad.process_chunk(_make_speech(amplitude=200))
    assert vad.is_speaking


def test_vad_adapts_to_loud_mic():
    vad = EnergyVAD(calibration_frames=10)
    _calibrate(vad, noise_func=lambda: _make_low_noise(amplitude=800), frames=10)
    assert vad.threshold > 500
    for _ in range(10):
        vad.process_chunk(_make_low_noise(amplitude=800))
    assert not vad.is_speaking


def test_vad_rms_empty():
    assert EnergyVAD._rms(b"") == 0.0
    assert EnergyVAD._rms(b"\x00") == 0.0


# ── TTS tests ──

def test_tts_extract_stream_audio():
    """_extract_stream_audio correctly decodes base64 PCM from SSE chunk."""
    from lampgo.voice.tts import _extract_stream_audio

    fake_pcm = b"\x00\x01\x02\x03" * 100
    b64 = base64.b64encode(fake_pcm).decode()
    chunk = {"choices": [{"delta": {"audio": {"data": b64}}}]}
    result = _extract_stream_audio(chunk)
    assert result == fake_pcm


def test_tts_extract_stream_audio_missing():
    from lampgo.voice.tts import _extract_stream_audio

    assert _extract_stream_audio({"choices": []}) is None
    assert _extract_stream_audio({"choices": [{"delta": {}}]}) is None
    assert _extract_stream_audio({"choices": [{"delta": {"audio": {}}}]}) is None


def test_tts_sample_rate():
    from lampgo.voice.tts import TTS_SAMPLE_RATE
    assert TTS_SAMPLE_RATE == 24000


# ── STT tests ──

def test_stt_extract_text():
    from lampgo.voice.stt import _extract_text

    assert _extract_text({"choices": [{"message": {"content": "你好世界"}}]}) == "你好世界"
    assert _extract_text({"choices": []}) == ""
    assert _extract_text({
        "choices": [{
            "message": {
                "content": [
                    {"type": "text", "text": "你好"},
                    {"type": "text", "text": "世界"},
                ]
            }
        }]
    }) == "你好 世界"


# ── Stream chat tests ──

def test_sentence_split_regex():
    from lampgo.voice.stream_chat import SENTENCE_ENDS
    assert SENTENCE_ENDS.search("你好。") is not None
    assert SENTENCE_ENDS.search("hello!") is not None
    assert SENTENCE_ENDS.search("问号？") is not None
    assert SENTENCE_ENDS.search("no end here") is None


@pytest.mark.asyncio
async def test_stream_chat_delta_extraction():
    from lampgo.voice.stream_chat import _extract_delta

    chunk = {"choices": [{"delta": {"content": "你好"}}]}
    assert _extract_delta(chunk) == "你好"
    assert _extract_delta({"choices": []}) == ""
    assert _extract_delta({"choices": [{"delta": {}}]}) == ""


# ── AudioPlayback tests ──

def test_audio_playback_queue():
    """AudioPlayback accepts and signals finish without sounddevice."""
    from lampgo.voice.audio import AudioPlayback

    player = AudioPlayback(sample_rate=24000)
    player.feed(b"\x00" * 100)
    player.feed(b"\x01" * 100)
    player.finish()
    assert player._queue.qsize() == 3  # 2 data + 1 None sentinel
