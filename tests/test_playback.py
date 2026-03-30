"""Playback/config regression tests."""

from __future__ import annotations

import pytest

from lampgo.core.config import LampgoConfig, MotionConfig, SafetyConfig
from lampgo.core.events import EventBus
from lampgo.core.led import LEDConfig, LEDController
from lampgo.core.motion import MotionRuntime
from lampgo.core.safety import SafetyKernel
from lampgo.skills.base import SkillContext
from lampgo.skills.builtin.playback_skills import PlayRecordingSkill, load_recording
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


def test_safe_position_constant():
    assert get_safe_position() == {
        "base_yaw": 0.0,
        "base_pitch": -44.68431771894094,
        "elbow_pitch": 82.83261802575109,
        "wrist_roll": 5.431619786614931,
        "wrist_pitch": 3.0620467365028077,
    }
