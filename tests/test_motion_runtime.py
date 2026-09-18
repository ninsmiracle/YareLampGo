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


def test_recovery_frame_can_escape_physical_edge_outside_normal_software_limit():
    from lampgo.core.config import SafetyConfig
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState

    safety = SafetyKernel(SafetyConfig(max_velocity=120.0))
    current = JointState(positions={"wrist_pitch": -80.0})

    normal = safety.validate_frame(current, {"wrist_pitch": -79.9}, dt=0.02)
    recovery = safety.validate_recovery_frame(
        current,
        {"wrist_pitch": -70.0},
        {"wrist_pitch": -4.9},
        dt=0.02,
        max_velocity=5.0,
    )
    away_from_safe = safety.validate_recovery_frame(
        current,
        {"wrist_pitch": -80.1},
        {"wrist_pitch": -4.9},
        dt=0.02,
    )

    assert normal["wrist_pitch"] == -45.0
    assert recovery["wrist_pitch"] == -79.9
    assert away_from_safe["wrist_pitch"] == -80.0


def test_recovery_command_builds_bounded_lead_over_static_feedback():
    from lampgo.core.config import SafetyConfig
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState

    safety = SafetyKernel(SafetyConfig(max_velocity=120.0))
    actual = JointState(positions={"base_pitch": -60.0})
    command = {"base_pitch": -60.0}

    for _ in range(40):
        command = safety.validate_recovery_frame(
            actual,
            {"base_pitch": 0.0},
            {"base_pitch": 0.0},
            dt=0.02,
            max_velocity=15.0,
            command_reference=command,
            max_command_lead=8.0,
        )

    assert command["base_pitch"] == -52.0


def test_motion_recovery_overcomes_deadband_with_bounded_command_lead():
    from lampgo.core.config import MotionConfig, SafetyConfig
    from lampgo.core.motion import MotionRuntime
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState

    class DeadbandRecoveryHal:
        motor_names = ["base_pitch"]

        def __init__(self) -> None:
            self.recovery_required = True
            self.position = -2.0
            self.command_samples: list[tuple[float, float]] = []
            self.completed = False
            self.aborted = False

        def read_recovery_start(self) -> dict[str, float]:
            return {"base_pitch": self.position}

        def prepare_recovery(self, frames: list[dict[str, float]]) -> dict[str, float]:
            assert frames
            self.recovery_required = False
            return {"base_pitch": self.position}

        def read_positions(self) -> JointState:
            return JointState(positions={"base_pitch": self.position})

        def write_recovery_positions(self, positions: dict[str, float]) -> None:
            command = positions["base_pitch"]
            before = self.position
            self.command_samples.append((before, command))
            error = command - before
            # Model static friction/deadband: a one-tick 0.1 degree command
            # cannot move the joint, but a bounded accumulated lead can.
            if abs(error) >= 0.35:
                self.position += 0.1 if error > 0 else -0.1

        def complete_recovery(self) -> None:
            self.completed = True

        def abort_recovery(self) -> None:
            self.aborted = True
            self.recovery_required = True

    hal = DeadbandRecoveryHal()
    motion = MotionRuntime(
        hal,
        SafetyKernel(SafetyConfig(max_velocity=120.0)),
        MotionConfig(tick_rate_hz=50, breathing_enabled=False),
    )
    frames = motion.prepare_recovery({"base_pitch": 0.0}, max_velocity=15.0, fps=50)
    try:
        done = motion.stream_recovery_frames(frames, fps=50)
        assert done.wait(timeout=2.0)
        assert _wait_for(lambda: abs(hal.position) < 1.0, timeout=0.5)
        assert max(abs(command - actual) for actual, command in hal.command_samples) >= 0.35
        assert max(abs(command - actual) for actual, command in hal.command_samples) <= 8.0
        assert motion.recovery_error is None
        motion.complete_recovery()
        assert hal.completed is True
        assert hal.aborted is False
    finally:
        motion.stop()


def test_motion_recovery_ignores_stalled_joint_already_within_final_tolerance():
    from lampgo.core.config import MotionConfig, SafetyConfig
    from lampgo.core.motion import MotionRuntime
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState

    class NearTargetJointHal:
        motor_names = ["base_pitch", "wrist_roll"]

        def __init__(self) -> None:
            self.recovery_required = True
            self.positions = {"base_pitch": -10.0, "wrist_roll": 1.15}
            self.abort_count = 0

        def read_recovery_start(self) -> dict[str, float]:
            return dict(self.positions)

        def prepare_recovery(self, frames: list[dict[str, float]]) -> dict[str, float]:
            assert frames
            self.recovery_required = False
            return dict(self.positions)

        def read_positions(self) -> JointState:
            return JointState(positions=dict(self.positions))

        def write_recovery_positions(self, positions: dict[str, float]) -> None:
            # The load-bearing joint follows the command.  wrist_roll models
            # the logged case: static at 1.15 degrees from target, already
            # inside the return_safe completion envelope.
            error = positions["base_pitch"] - self.positions["base_pitch"]
            self.positions["base_pitch"] += max(-0.5, min(0.5, error))

        def abort_recovery(self) -> None:
            self.abort_count += 1
            self.recovery_required = True

    hal = NearTargetJointHal()
    motion = MotionRuntime(
        hal,
        SafetyKernel(SafetyConfig(max_velocity=120.0)),
        MotionConfig(tick_rate_hz=50, breathing_enabled=False),
    )
    frames = motion.prepare_recovery(
        {"base_pitch": 0.0, "wrist_roll": 0.0},
        max_velocity=15.0,
        fps=50,
    )
    try:
        done = motion.stream_recovery_frames(frames, fps=50)
        assert done.wait(timeout=1.5)
        assert hal.abort_count == 0
        assert motion.recovery_error is None
        assert abs(hal.positions["base_pitch"]) <= 5.0
        assert hal.positions["wrist_roll"] == 1.15
    finally:
        motion.stop()


def test_motion_recovery_keeps_holding_target_while_loaded_joint_is_progressing():
    from lampgo.core.config import MotionConfig, SafetyConfig
    from lampgo.core.motion import MotionRuntime
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState

    class LoadedRecoveryHal:
        motor_names = ["elbow_pitch"]

        def __init__(self) -> None:
            self.recovery_required = True
            self.position = -10.0
            self.abort_count = 0

        def read_recovery_start(self) -> dict[str, float]:
            return {"elbow_pitch": self.position}

        def prepare_recovery(self, frames: list[dict[str, float]]) -> dict[str, float]:
            assert frames
            self.recovery_required = False
            return {"elbow_pitch": self.position}

        def read_positions(self) -> JointState:
            return JointState(positions={"elbow_pitch": self.position})

        def write_recovery_positions(self, positions: dict[str, float]) -> None:
            error = positions["elbow_pitch"] - self.position
            self.position += max(-0.1, min(0.1, error))

        def abort_recovery(self) -> None:
            self.abort_count += 1
            self.recovery_required = True

    hal = LoadedRecoveryHal()
    motion = MotionRuntime(
        hal,
        SafetyKernel(SafetyConfig(max_velocity=120.0)),
        MotionConfig(tick_rate_hz=50, breathing_enabled=False),
    )
    # A tiny ordinary-stream settle timeout makes this test fail immediately
    # if recovery accidentally starts using a wall-clock settle cutoff again.
    motion._STREAM_SETTLE_TIMEOUT_S = 0.1
    frames = motion.prepare_recovery({"elbow_pitch": 0.0}, max_velocity=15.0, fps=50)
    try:
        done = motion.stream_recovery_frames(frames, fps=50)
        time.sleep(0.9)
        assert done.is_set() is False
        assert hal.position < -5.0
        assert done.wait(timeout=1.5)
        assert abs(hal.position) <= 5.0
        assert hal.abort_count == 0
        assert motion.recovery_error is None
    finally:
        motion.stop()


def test_motion_recovery_stall_warning_keeps_torque_and_target_active():
    from lampgo.core.config import MotionConfig, SafetyConfig
    from lampgo.core.motion import MotionRuntime
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState

    class StalledRecoveryHal:
        motor_names = ["base_pitch"]

        def __init__(self) -> None:
            self.recovery_required = True
            self.abort_count = 0
            self.commands: list[float] = []

        def read_recovery_start(self) -> dict[str, float]:
            return {"base_pitch": -10.0}

        def prepare_recovery(self, frames: list[dict[str, float]]) -> dict[str, float]:
            self.recovery_required = False
            return {"base_pitch": -10.0}

        def read_positions(self) -> JointState:
            return JointState(positions={"base_pitch": -10.0})

        def write_recovery_positions(self, positions: dict[str, float]) -> None:
            self.commands.append(positions["base_pitch"])

        def abort_recovery(self) -> None:
            self.abort_count += 1
            self.recovery_required = True

    hal = StalledRecoveryHal()
    motion = MotionRuntime(
        hal,
        SafetyKernel(SafetyConfig(max_velocity=120.0)),
        MotionConfig(tick_rate_hz=50, breathing_enabled=False),
    )
    motion._RECOVERY_STALL_TIMEOUT_S = 0.3
    frames = motion.prepare_recovery({"base_pitch": 0.0}, max_velocity=15.0, fps=50)
    try:
        done = motion.stream_recovery_frames(frames, fps=50)
        time.sleep(0.8)
        assert done.is_set() is False
        assert hal.abort_count == 0
        assert motion.recovery_error is None
        assert max(command + 10.0 for command in hal.commands) <= 8.0
    finally:
        motion.stop()


def test_motion_recovery_feedback_warning_does_not_release_torque():
    from lampgo.core.config import MotionConfig, SafetyConfig
    from lampgo.core.motion import MotionRuntime
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState

    class EscapingRecoveryHal:
        motor_names = ["base_pitch"]

        def __init__(self) -> None:
            self.recovery_required = True
            self.position = -10.0
            self.abort_count = 0

        def read_recovery_start(self) -> dict[str, float]:
            return {"base_pitch": self.position}

        def prepare_recovery(self, frames: list[dict[str, float]]) -> dict[str, float]:
            self.recovery_required = False
            return {"base_pitch": self.position}

        def read_positions(self) -> JointState:
            return JointState(positions={"base_pitch": self.position})

        def write_recovery_positions(self, positions: dict[str, float]) -> None:
            del positions
            self.position = -12.0

        def abort_recovery(self) -> None:
            self.abort_count += 1
            self.recovery_required = True

    hal = EscapingRecoveryHal()
    motion = MotionRuntime(
        hal,
        SafetyKernel(SafetyConfig(max_velocity=120.0)),
        MotionConfig(tick_rate_hz=50, breathing_enabled=False),
    )
    frames = motion.prepare_recovery({"base_pitch": 0.0}, max_velocity=15.0, fps=50)
    try:
        done = motion.stream_recovery_frames(frames, fps=50)
        time.sleep(0.3)
        assert done.is_set() is False
        assert hal.abort_count == 0
        assert motion.recovery_error is None
    finally:
        motion.stop()


def test_recovery_lead_clamp_never_reverses_after_feedback_retreats():
    from lampgo.core.config import SafetyConfig
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState

    safety = SafetyKernel(SafetyConfig(max_velocity=120.0))
    for direction in (1, -1):
        previous = -12.0 * direction
        for actual in (-21.0, -20.1, -21.5, -19.0, -18.0):
            command = safety.validate_recovery_frame(
                JointState(positions={"base_pitch": actual * direction}),
                {"base_pitch": 0.0}, {"base_pitch": 0.0}, 0.02,
                max_velocity=30.0, command_reference={"base_pitch": previous}, max_command_lead=8.0,
            )["base_pitch"]
            assert (command - previous) * direction >= 0  # P4 directed() invariant
            assert abs(command - previous) <= 0.6 + 1e-9
            if abs(previous - actual * direction) > 8:
                assert command == previous  # hold, never increase an existing lead
            else:
                assert abs(command - actual * direction) <= 8 + 1e-9
            previous = command


def test_recovery_transport_failure_finishes_waiter_and_stops_retries():
    import threading
    from lampgo.core.config import MotionConfig, SafetyConfig
    from lampgo.core.motion import MotionRuntime
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState

    for failure in ("read", "write"):
        class Hal:
            motor_names = ["base_pitch"]
            recovery_required = True
            fail = threading.Event()
            attempts = 0
            failures = 0

            def read_recovery_start(self):
                return {"base_pitch": -20.0}

            def prepare_recovery(self, frames):
                return self.read_recovery_start()

            def read_positions(self):
                if failure == "read" and self.fail.is_set():
                    self.failures += 1
                    raise RuntimeError("P4 servo telemetry is stale")
                return JointState(positions=self.read_recovery_start())

            def write_recovery_positions(self, positions):
                self.attempts += 1
                if failure == "write" and self.fail.is_set():
                    self.failures += 1
                    raise RuntimeError("P4 recovery has not been prepared: hard_fault")

            def write_positions(self, *args, **kwargs):
                raise AssertionError("must not fall back to ordinary motion")

            def disable_torque(self):
                raise AssertionError("failure must not issue torque-off")

        hal = Hal()
        motion = MotionRuntime(hal, SafetyKernel(SafetyConfig()), MotionConfig(breathing_enabled=False))
        frames = motion.prepare_recovery({"base_pitch": 0.0}, max_velocity=30, fps=50)
        try:
            done = motion.stream_recovery_frames(frames, fps=50)
            assert _wait_for(lambda: hal.attempts > 3)
            hal.fail.set()
            assert done.wait(1)
            assert not motion.is_running
            assert motion.status.stalled
            assert "Recovery" in motion.recovery_error
            attempts = hal.attempts
            time.sleep(0.1)
            assert hal.attempts == attempts
            assert hal.failures == 1
            assert motion._recovery_target is None
            # An explicit new recovery performs preflight again and clears the
            # old failure; reconnect alone never resumes the old trajectory.
            motion.stop()
            hal.fail.clear()
            frames = motion.prepare_recovery({"base_pitch": 0.0}, max_velocity=30, fps=50)
            assert motion.recovery_error is None
        finally:
            motion.stop()


def test_cancel_during_recovery_settling_does_not_report_target_reached(monkeypatch):
    import threading
    from unittest.mock import Mock
    from lampgo.core import motion as module
    from lampgo.core.config import MotionConfig, SafetyConfig
    from lampgo.core.safety import SafetyKernel
    from lampgo.core.types import JointState

    settling = threading.Event()
    logger = Mock()
    logger.info.side_effect = lambda event, **kw: settling.set() if event == "motion.recovery_holding_target" else None
    monkeypatch.setattr(module, "logger", logger)

    class Hal:
        motor_names = ["base_pitch"]
        recovery_required = True

        def read_recovery_start(self): return {"base_pitch": -10.0}
        def prepare_recovery(self, frames): return self.read_recovery_start()
        def read_positions(self): return JointState(positions=self.read_recovery_start())
        def write_recovery_positions(self, positions): pass

    motion = module.MotionRuntime(Hal(), SafetyKernel(SafetyConfig()), MotionConfig(breathing_enabled=False))
    frames = motion.prepare_recovery({"base_pitch": 0.0}, max_velocity=30, fps=50)
    try:
        done = motion.stream_recovery_frames(frames, fps=50)
        assert settling.wait(1)
        motion.stop_immediate()
        assert done.wait(1)
        time.sleep(0.08)
        assert not any(c.args[0] == "motion.recovery_target_reached" for c in logger.info.call_args_list)
    finally:
        motion.stop()
