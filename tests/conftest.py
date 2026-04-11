"""Shared fixtures for lampgo tests — mock HAL, configs, etc."""

from __future__ import annotations

import pytest

from lampgo.core.config import DeviceConfig, LampgoConfig, MotionConfig, SafetyConfig
from lampgo.core.hal import HardwareAbstraction
from lampgo.core.types import JointState


class MockHAL(HardwareAbstraction):
    """In-memory HAL for testing without real hardware."""

    def __init__(self) -> None:
        config = DeviceConfig(motor_port="/dev/null")
        super().__init__(config)
        self._positions: dict[str, float] = {
            "base_yaw": 0.0,
            "base_pitch": 0.0,
            "elbow_pitch": 0.0,
            "wrist_roll": 0.0,
            "wrist_pitch": 0.0,
        }
        self._connected = False
        self._write_log: list[dict[str, float]] = []
        self._move_time_log: list[int] = []
        self._torque_disabled = False
        self._torque_enabled = False

    def connect(self, calibrate: bool = True) -> None:
        self._connected = True
        self._torque_disabled = False
        self._torque_enabled = True

    def disconnect(self) -> None:
        self._connected = False

    def read_positions(self) -> JointState:
        return JointState(positions=dict(self._positions))

    def write_positions(self, positions: dict[str, float], move_time_ms: int = 0) -> None:
        self._positions.update(positions)
        self._write_log.append(dict(positions))
        self._move_time_log.append(move_time_ms)

    def disable_torque(self) -> None:
        self._torque_disabled = True
        self._torque_enabled = False

    def enable_torque(self) -> None:
        self._torque_enabled = True
        self._torque_disabled = False

    @property
    def write_log(self) -> list[dict[str, float]]:
        return self._write_log

    @property
    def move_time_log(self) -> list[int]:
        return self._move_time_log

    @property
    def torque_disabled(self) -> bool:
        return self._torque_disabled

    @property
    def torque_enabled(self) -> bool:
        return self._torque_enabled


@pytest.fixture
def mock_hal() -> MockHAL:
    hal = MockHAL()
    hal.connect()
    return hal


@pytest.fixture
def safety_config() -> SafetyConfig:
    return SafetyConfig()


@pytest.fixture
def motion_config() -> MotionConfig:
    return MotionConfig(tick_rate_hz=100)


@pytest.fixture
def lampgo_config() -> LampgoConfig:
    return LampgoConfig(device=DeviceConfig(motor_port="/dev/null"))
