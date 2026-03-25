"""Tests for voice module components."""

from __future__ import annotations

import struct

from lampgo.voice.vad import EnergyVAD


def _make_silence(n_samples: int = 480) -> bytes:
    return struct.pack(f"<{n_samples}h", *([0] * n_samples))


def _make_speech(n_samples: int = 480, amplitude: int = 5000) -> bytes:
    import math
    samples = [int(amplitude * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n_samples)]
    return struct.pack(f"<{n_samples}h", *samples)


def test_vad_silence():
    vad = EnergyVAD(energy_threshold=300, silence_frames=5, min_speech_frames=2)
    for _ in range(20):
        vad.process_chunk(_make_silence())
    assert not vad.is_speaking


def test_vad_speech_detection():
    vad = EnergyVAD(energy_threshold=300, silence_frames=5, min_speech_frames=2)
    for _ in range(5):
        vad.process_chunk(_make_speech())
    assert vad.is_speaking


def test_vad_speech_then_silence():
    vad = EnergyVAD(energy_threshold=300, silence_frames=3, min_speech_frames=2)
    for _ in range(5):
        vad.process_chunk(_make_speech())
    assert vad.is_speaking

    for _ in range(10):
        vad.process_chunk(_make_silence())
    assert not vad.is_speaking


def test_vad_reset():
    vad = EnergyVAD()
    for _ in range(10):
        vad.process_chunk(_make_speech())
    assert vad.is_speaking
    vad.reset()
    assert not vad.is_speaking


def test_vad_rms_empty():
    assert EnergyVAD._rms(b"") == 0.0
    assert EnergyVAD._rms(b"\x00") == 0.0
