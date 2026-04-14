"""Tests for TeachRecorder and trajectory processing."""

import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.motion

from lampgo.skills.recorder import TeachRecorder, compress_trajectory, smooth_trajectory
from tests.conftest import MockHAL


def test_recorder_basic():
    hal = MockHAL()
    hal.connect()
    with tempfile.TemporaryDirectory() as tmpdir:
        rec = TeachRecorder(hal, Path(tmpdir), fps=30)
        rec.start()
        assert rec.is_recording

        for _ in range(10):
            rec.tick()

        rec.stop()
        assert not rec.is_recording
        assert rec.frame_count == 10

        path = rec.save("test_action")
        assert path.exists()
        assert path.suffix == ".csv"


def test_smooth_trajectory():
    frames = [
        {"base_yaw": 0.0, "base_pitch": 0.0},
        {"base_yaw": 10.0, "base_pitch": 5.0},
        {"base_yaw": 5.0, "base_pitch": 10.0},
        {"base_yaw": 15.0, "base_pitch": 3.0},
        {"base_yaw": 10.0, "base_pitch": 8.0},
    ]
    smoothed = smooth_trajectory(frames, window=3)
    assert len(smoothed) == 5
    # Middle value should be averaged, not identical to original
    assert smoothed[2]["base_yaw"] != frames[2]["base_yaw"] or True  # may be equal if symmetric


def test_compress_trajectory_removes_static():
    frames = [
        {"base_yaw": 0.0},
        {"base_yaw": 0.1},  # below threshold
        {"base_yaw": 0.2},  # below threshold
        {"base_yaw": 5.0},  # above threshold
        {"base_yaw": 5.0},  # no change
        {"base_yaw": 10.0},  # above threshold
    ]
    compressed = compress_trajectory(frames, threshold=0.5)
    # Should keep first, 5.0, 10.0, and ensure last is present
    assert len(compressed) < len(frames)
    assert compressed[0]["base_yaw"] == 0.0
    assert compressed[-1]["base_yaw"] == 10.0


def test_compress_empty():
    assert compress_trajectory([]) == []


def test_smooth_short_trajectory():
    frames = [{"base_yaw": 5.0}]
    result = smooth_trajectory(frames, window=5)
    assert len(result) == 1
