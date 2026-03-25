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

    def connect(self, calibrate: bool = True) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def read_positions(self) -> JointState:
        return JointState(positions=dict(self._positions))

    def write_positions(self, positions: dict[str, float]) -> None:
        self._positions.update(positions)
        self._write_log.append(dict(positions))

    @property
    def write_log(self) -> list[dict[str, float]]:
        return self._write_log


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
