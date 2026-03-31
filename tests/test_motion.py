"""Tests for MotionRuntime — trapezoidal interpolation in a control thread."""

import time

from lampgo.core.config import MotionConfig, SafetyConfig
from lampgo.core.motion import MotionRuntime
from lampgo.core.safety import SafetyKernel
from lampgo.core.types import MotionTarget
from tests.conftest import MockHAL


def test_move_to_reaches_target():
    hal = MockHAL()
    hal.connect()
    safety = SafetyKernel(SafetyConfig())
    config = MotionConfig(tick_rate_hz=200)
    motion = MotionRuntime(hal, safety, config)
    motion.start()

    try:
        target = MotionTarget(joints={"base_yaw": 30.0})
        done = motion.move_to(target)
        done.wait(timeout=5.0)

        assert done.is_set(), "Motion did not complete within timeout"
        final = hal.read_positions()
        assert abs(final.positions["base_yaw"] - 30.0) < 1.0
    finally:
        motion.stop()


def test_stop_immediate():
    hal = MockHAL()
    hal.connect()
    safety = SafetyKernel(SafetyConfig())
    config = MotionConfig(tick_rate_hz=200)
    motion = MotionRuntime(hal, safety, config)
    motion.start()

    try:
        target = MotionTarget(joints={"base_yaw": 100.0}, max_velocity=10.0)
        motion.move_to(target)
        time.sleep(0.1)
        motion.stop_immediate()
        time.sleep(0.05)

        assert motion.status.is_done
    finally:
        motion.stop()


def test_stream_frames():
    hal = MockHAL()
    hal.connect()
    safety = SafetyKernel(SafetyConfig())
    config = MotionConfig(tick_rate_hz=200)
    motion = MotionRuntime(hal, safety, config)
    motion.start()

    try:
        frames = [
            {"base_yaw": 10.0},
            {"base_yaw": 20.0},
            {"base_yaw": 30.0},
        ]
        done = motion.stream_frames(frames, fps=50)
        done.wait(timeout=3.0)
        assert done.is_set()
        final = hal.read_positions()
        assert abs(final.positions["base_yaw"] - 30.0) < 0.1
    finally:
        motion.stop()


def test_stream_frames_preserves_recorded_frame_steps():
    hal = MockHAL()
    hal.connect()
    safety = SafetyKernel(SafetyConfig(max_velocity=60.0))
    config = MotionConfig(tick_rate_hz=200)
    motion = MotionRuntime(hal, safety, config)
    motion.start()

    try:
        frames = [
            {"base_yaw": 4.0},
            {"base_yaw": 8.0},
        ]
        done = motion.stream_frames(frames, fps=20)
        done.wait(timeout=3.0)

        assert done.is_set()
        final = hal.read_positions()
        assert abs(final.positions["base_yaw"] - 8.0) < 0.1
        assert {"base_yaw": 4.0} in hal.write_log
        assert {"base_yaw": 8.0} in hal.write_log
    finally:
        motion.stop()


def test_estop_halts_motion():
    hal = MockHAL()
    hal.connect()
    safety = SafetyKernel(SafetyConfig())
    config = MotionConfig(tick_rate_hz=200)
    motion = MotionRuntime(hal, safety, config)
    motion.start()

    try:
        target = MotionTarget(joints={"base_yaw": 100.0}, max_velocity=10.0)
        motion.move_to(target)
        time.sleep(0.05)
        safety.estop("test")
        pos_before = hal.read_positions().positions.get("base_yaw", 0.0)
        time.sleep(0.2)
        pos_after = hal.read_positions().positions.get("base_yaw", 0.0)
        assert abs(pos_after - pos_before) < 1.0, "Motion should halt during estop"
    finally:
        motion.stop()
