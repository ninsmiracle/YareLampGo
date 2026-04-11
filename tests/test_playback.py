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
    PlayRecordingSkill,
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
async def test_play_recording_uses_stream_frames_not_move_to_waypoints(tmp_path, monkeypatch):
    """PlayRecordingSkill must use stream_frames (trajectory-based paradigm).

    move_to waypoints reset joint velocities on every call and destroy the
    natural acceleration/deceleration captured in the recording.
    """
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

    stream_calls: list = []
    original_stream = motion.stream_frames

    def _capture_stream(frames, fps=30):
        stream_calls.append((len(frames), fps))
        return original_stream(frames, fps)

    monkeypatch.setattr(motion, "stream_frames", _capture_stream)

    try:
        skill = PlayRecordingSkill(tmp_path)
        ctx = SkillContext(
            motion=motion,
            led=LEDController(LEDConfig()),
            events=EventBus(),
            state=hal.read_positions(),
        )
        result = await skill.execute(ctx, name="wave")
        assert result.status == "ok"
        assert result.data["returned_safe"] is True
        # stream_frames must be called exactly once with all 3 frames
        assert len(stream_calls) == 1
        assert stream_calls[0][0] == 3
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


def test_play_recording_result_has_no_style_field(tmp_path):
    """Result data must not contain 'style' or 'safety_path' — those belonged to the
    old move_to-based implementation and are no longer meaningful."""
    import asyncio
    from lampgo.core.config import MotionConfig, SafetyConfig
    from lampgo.core.events import EventBus
    from lampgo.core.led import LEDConfig, LEDController
    from lampgo.core.motion import MotionRuntime
    from lampgo.core.safety import SafetyKernel
    from lampgo.skills.base import SkillContext
    from tests.conftest import MockHAL

    path = tmp_path / "sample.csv"
    path.write_text("timestamp,base_yaw.pos\n0.0,0.0\n0.1,5.0\n")

    hal = MockHAL()
    hal.connect()
    motion = MotionRuntime(hal, SafetyKernel(SafetyConfig()), MotionConfig(tick_rate_hz=100))
    motion.start()
    try:
        skill = PlayRecordingSkill(tmp_path)
        ctx = SkillContext(motion=motion, led=LEDController(LEDConfig()), events=EventBus(), state=hal.read_positions())
        result = asyncio.get_event_loop().run_until_complete(skill.execute(ctx, name="sample"))
        assert "style" not in (result.data or {})
        assert "safety_path" not in (result.data or {})
    finally:
        motion.stop()
