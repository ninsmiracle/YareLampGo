from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import numpy as np

from lampgo.core.types import JointState
from lampgo.perception.music import BeatGate, DancePhraseRenderer, MusicFeatureExtractor, MusicFeatures
from lampgo.skills.builtin.music_skills import DanceToMusicSkill


def test_music_feature_extractor_decodes_pcm_and_energy_bands():
    sample_rate = 16_000
    t = np.arange(sample_rate // 2, dtype=np.float32) / sample_rate
    signal = 0.45 * np.sin(2.0 * np.pi * 80.0 * t) + 0.12 * np.sin(2.0 * np.pi * 4_000.0 * t)
    pcm = (np.clip(signal, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    extractor = MusicFeatureExtractor(sample_rate=sample_rate, channels=1, window_ms=100)
    features = extractor.push_pcm(pcm, timestamp=10.0)

    assert features
    assert max(item.rms for item in features) > 0.2
    assert max(item.bass_energy for item in features) > max(item.mid_energy for item in features)
    assert features[0].timestamp == 10.0


def test_beat_gate_skips_fast_music_by_stride_instead_of_tiny_motion():
    gate = BeatGate(beat_stride=2, min_accent_interval_s=0.0, accent_threshold=0.1)

    decisions = [
        gate.consider(
            MusicFeatures(
                timestamp=idx * 0.5,
                rms=0.2,
                bass_energy=0.1,
                mid_energy=0.1,
                treble_energy=0.1,
                onset_score=0.5,
            )
        )
        for idx in range(4)
    ]

    assert [decision.accent for decision in decisions] == [True, False, True, False]
    assert decisions[1].reason == "stride_skip"


def test_dance_phrase_renderer_drops_invisible_micro_motion():
    renderer = DancePhraseRenderer(fps=20, min_motion_amplitude_deg=20.0)
    features = [
        MusicFeatures(
            timestamp=1.0,
            rms=0.05,
            bass_energy=0.02,
            mid_energy=0.01,
            treble_energy=0.01,
            onset_score=0.0,
        )
    ]

    assert renderer.render(anchor={}, features=features, beat=None) == []


def test_dance_phrase_renderer_keeps_clear_accent_motion():
    renderer = DancePhraseRenderer(fps=20, min_motion_amplitude_deg=2.5)
    features = [
        MusicFeatures(
            timestamp=1.0,
            rms=0.2,
            bass_energy=0.08,
            mid_energy=0.02,
            treble_energy=0.03,
            onset_score=0.6,
        )
    ]
    beat = BeatGate(beat_stride=1).consider(features[0])

    frames = renderer.render(anchor={"base_yaw": 0.0, "base_pitch": 0.0}, features=features, beat=beat)

    assert frames
    joints = {joint for frame in frames for joint in frame}
    assert {"base_yaw", "base_pitch", "wrist_roll", "wrist_pitch"}.issubset(joints)
    assert max(abs(frame.get("base_yaw", 0.0)) for frame in frames) >= 2.5
    assert max(abs(frame.get("base_pitch", 0.0)) for frame in frames) >= 2.5


def test_dance_to_music_skill_runs_with_synthetic_source():
    class FakeMotion:
        is_running = True

        def __init__(self) -> None:
            self.current_state = JointState({"base_yaw": 0.0, "base_pitch": 0.0, "wrist_roll": 0.0})
            self.streams: list[list[dict[str, float]]] = []
            self.stopped = False

        def stream_frames(self, frames, fps=30, playback_mode="cleaned"):
            del fps, playback_mode
            self.streams.append(frames)
            done = threading.Event()
            done.set()
            return done

        def stop_smooth(self):
            self.stopped = True

    class FakeLed:
        is_connected = False

        def set_mode(self, mode):
            del mode
            return True

    async def run() -> None:
        motion = FakeMotion()
        ctx = SimpleNamespace(motion=motion, led=FakeLed(), state=motion.current_state)
        result = await DanceToMusicSkill().execute(ctx, source="synthetic", duration=0.35, led=False)

        assert result.status == "ok"
        assert result.data["source"] == "synthetic"
        assert result.data["phrases"] >= 1
        assert motion.streams
        assert motion.stopped is True

    asyncio.run(run())
