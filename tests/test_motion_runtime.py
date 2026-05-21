"""Motion runtime behavior tests."""

from __future__ import annotations

import time


def _wait_for(predicate, timeout: float = 1.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_motion_velocity_clamp_advances_from_last_command_when_hardware_lags():
    from lampgo.core.config import MotionConfig, SafetyConfig
    from lampgo.core.motion import MotionRuntime
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState, MotionTarget

    class LaggingHal:
        def __init__(self) -> None:
            self.commands: list[float] = []

        def read_positions(self) -> JointState:
            return JointState(positions={"base_pitch": -90.0})

        def write_positions(self, positions: dict[str, float], move_time_ms: int = 0) -> None:
            self.commands.append(positions["base_pitch"])

    hal = LaggingHal()
    motion = MotionRuntime(
        hal,
        SafetyKernel(SafetyConfig(max_velocity=120.0)),
        MotionConfig(tick_rate_hz=50, breathing_enabled=False),
    )
    motion.start()
    try:
        motion.move_to(MotionTarget(joints={"base_pitch": -40.0}, max_velocity=60.0, anticipation=False))
        assert _wait_for(lambda: len(hal.commands) >= 25, timeout=1.5)
        assert max(hal.commands) > -80.0
    finally:
        motion.stop()
