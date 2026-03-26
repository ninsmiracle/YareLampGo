"""Tests for SafetyKernel."""

from lampgo.core.config import SafetyConfig
from lampgo.core.safety import SafetyKernel
from lampgo.core.types import JointState, MotionTarget, SafetyRejection


def test_validate_target_within_limits():
    kernel = SafetyKernel(SafetyConfig())
    current = JointState(positions={"base_yaw": 0.0})
    target = MotionTarget(joints={"base_yaw": 50.0})
    result = kernel.validate_target(current, target)
    assert isinstance(result, MotionTarget)
    assert result.joints["base_yaw"] == 50.0


def test_validate_target_clamps():
    kernel = SafetyKernel(SafetyConfig())
    current = JointState(positions={"base_yaw": 0.0})
    target = MotionTarget(joints={"base_yaw": 999.0})
    result = kernel.validate_target(current, target)
    assert isinstance(result, MotionTarget)
    assert result.joints["base_yaw"] == 150.0


def test_validate_target_unknown_joint():
    kernel = SafetyKernel(SafetyConfig())
    current = JointState(positions={})
    target = MotionTarget(joints={"nonexistent": 10.0})
    result = kernel.validate_target(current, target)
    assert isinstance(result, SafetyRejection)
    assert "unknown" in result.reason


def test_estop_blocks_targets():
    kernel = SafetyKernel(SafetyConfig())
    kernel.estop("test")
    assert kernel.is_estopped()

    current = JointState(positions={"base_yaw": 0.0})
    target = MotionTarget(joints={"base_yaw": 10.0})
    result = kernel.validate_target(current, target)
    assert isinstance(result, SafetyRejection)
    assert "e-stop" in result.reason


def test_estop_reset():
    kernel = SafetyKernel(SafetyConfig())
    kernel.estop("test")
    assert kernel.is_estopped()
    kernel.reset_estop()
    assert not kernel.is_estopped()


def test_validate_frame_clamps_position():
    kernel = SafetyKernel(SafetyConfig())
    current = JointState(positions={"base_yaw": 0.0})
    frame = {"base_yaw": 999.0}
    result = kernel.validate_frame(current, frame, dt=0.02)
    assert result["base_yaw"] <= 150.0


def test_validate_frame_clamps_velocity():
    kernel = SafetyKernel(SafetyConfig())
    current = JointState(positions={"base_yaw": 0.0})
    # Jump 100 degrees in 0.02s = 5000 deg/s, way over limit
    frame = {"base_yaw": 100.0}
    result = kernel.validate_frame(current, frame, dt=0.02)
    assert result["base_yaw"] < 100.0


def test_validate_frame_during_estop_holds_position():
    kernel = SafetyKernel(SafetyConfig())
    kernel.estop("test")
    current = JointState(positions={"base_yaw": 42.0})
    frame = {"base_yaw": 100.0}
    result = kernel.validate_frame(current, frame, dt=0.02)
    assert result["base_yaw"] == 42.0


def test_bus_disconnect_triggers_estop():
    kernel = SafetyKernel(SafetyConfig())
    kernel.report_bus_health(True)
    assert not kernel.is_estopped()
    for _ in range(kernel._BUS_FAIL_THRESHOLD):
        kernel.report_bus_health(False)
    assert kernel.is_estopped()
    assert kernel.last_estop_reason == "serial bus disconnected"
