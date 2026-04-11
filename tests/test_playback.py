"""Playback/config regression tests."""

from __future__ import annotations

import pytest

from lampgo.core.config import LampgoConfig, MotionConfig, SafetyConfig
from lampgo.core.events import EventBus
from lampgo.core.led import LEDConfig, LEDController
from lampgo.core.motion import MotionRuntime
from lampgo.core.safety import SafetyKernel
from lampgo.skills.base import SkillContext
from lampgo.skills.builtin.playback_skills import (
    FIRST_SEGMENT_MIN_TIMEOUT_S,
    PlayRecordingSkill,
    _segment_timeout_s,
    load_recording,
)
from lampgo.skills.builtin.motion_skills import get_safe_position
from tests.conftest import MockHAL


def test_load_recording_estimates_fps_from_frame_intervals(tmp_path):
    path = tmp_path / "sample.csv"
    path.write_text(
        "\n".join(
            [
                "timestamp,base_yaw.pos",
                "0.0,0.0",
                "0.1,5.0",
                "0.2,10.0",
            ]
        )
    )

    frames, fps = load_recording(path)

    assert fps == 10
    assert frames == [
        {"base_yaw": 0.0},
        {"base_yaw": 5.0},
        {"base_yaw": 10.0},
    ]


def test_home_on_start_disabled_by_default():
    assert LampgoConfig().home_on_start is False


@pytest.mark.asyncio
async def test_play_recording_returns_to_safe_position(tmp_path):
    path = tmp_path / "nod.csv"
    path.write_text(
        "\n".join(
            [
                "timestamp,base_yaw.pos",
                "0.0,10.0",
                "0.1,20.0",
            ]
        )
    )

    hal = MockHAL()
    hal.connect()
    motion = MotionRuntime(hal, SafetyKernel(SafetyConfig()), MotionConfig(tick_rate_hz=100))
    motion.start()

    try:
        skill = PlayRecordingSkill(tmp_path)
        ctx = SkillContext(
            motion=motion,
            led=LEDController(LEDConfig()),
            events=EventBus(),
            state=hal.read_positions(),
        )

        result = await skill.execute(ctx, name="nod")

        assert result.status == "ok"
        assert result.data["returned_safe"] is True
        safe = get_safe_position()
        positions = hal.read_positions().positions
        assert abs(positions["base_yaw"] - safe["base_yaw"]) < 0.5
        assert abs(positions["base_pitch"] - safe["base_pitch"]) < 0.5
    finally:
        motion.stop()


@pytest.mark.asyncio
async def test_play_recording_uses_move_to_path_not_stream_frames(tmp_path, monkeypatch):
    path = tmp_path / "wave.csv"
    path.write_text(
        "\n".join(
            [
                "timestamp,base_yaw.pos",
                "0.0,0.0",
                "0.1,5.0",
                "0.2,10.0",
            ]
        )
    )

    hal = MockHAL()
    hal.connect()
    motion = MotionRuntime(hal, SafetyKernel(SafetyConfig()), MotionConfig(tick_rate_hz=100))
    motion.start()

    def _fail_stream(*args, **kwargs):
        raise AssertionError("stream_frames should not be called by PlayRecordingSkill")

    monkeypatch.setattr(motion, "stream_frames", _fail_stream)

    try:
        skill = PlayRecordingSkill(tmp_path)
        ctx = SkillContext(
            motion=motion,
            led=LEDController(LEDConfig()),
            events=EventBus(),
            state=hal.read_positions(),
        )
        result = await skill.execute(ctx, name="wave", style="gentle", velocity=80)
        assert result.status == "ok"
        assert result.data["style"] == "gentle"
        assert result.data["safety_path"] == "validate_frame"
    finally:
        motion.stop()


def test_safe_position_constant():
    """SAFE_POSITION defaults to zeros until server injects calibration home."""
    pos = get_safe_position()
    assert set(pos.keys()) == {
        "base_yaw",
        "base_pitch",
        "elbow_pitch",
        "wrist_roll",
        "wrist_pitch",
    }
    assert pos == {
        "base_yaw": 0.0,
        "base_pitch": 0.0,
        "elbow_pitch": 0.0,
        "wrist_roll": 0.0,
        "wrist_pitch": 0.0,
    }


def test_segment_timeout_first_segment_has_extra_budget():
    start = {"base_yaw": 0.0}
    end = {"base_yaw": 180.0}
    velocity = 80.0

    first = _segment_timeout_s(start, end, velocity, is_first_segment=True)
    later = _segment_timeout_s(start, end, velocity, is_first_segment=False)

    assert first >= FIRST_SEGMENT_MIN_TIMEOUT_S
    assert first >= later
