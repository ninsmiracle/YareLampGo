"""Configuration models for lampgo — all validated via Pydantic."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class JointLimits(BaseModel):
    """Position limits for a single joint (degrees)."""

    min: float
    max: float


# Defaults derived from the legacy daemon's LIMITS dict.
DEFAULT_JOINT_LIMITS: dict[str, JointLimits] = {
    "base_yaw": JointLimits(min=-150.0, max=150.0),
    "base_pitch": JointLimits(min=-100.0, max=65.0),
    "elbow_pitch": JointLimits(min=-90.0, max=100.0),
    "wrist_roll": JointLimits(min=-75.0, max=75.0),
    "wrist_pitch": JointLimits(min=-45.0, max=100.0),
}


class MotorConfig(BaseModel):
    """Per-motor hardware configuration."""

    id: int
    model: str = "sts3215"


DEFAULT_MOTORS: dict[str, MotorConfig] = {
    "base_yaw": MotorConfig(id=1),
    "base_pitch": MotorConfig(id=2),
    "elbow_pitch": MotorConfig(id=3),
    "wrist_roll": MotorConfig(id=4),
    "wrist_pitch": MotorConfig(id=5),
}


class DeviceConfig(BaseModel):
    """Hardware connection settings."""

    motor_port: str = Field(description="Serial port for the Feetech motor bus, e.g. /dev/ttyUSB0")
    led_port: str = Field(default="", description="Serial port for ESP32 LED controller (empty = disabled)")
    lamp_id: str = Field(default="AL01", description="Device identity used for calibration file lookup")
    motors: dict[str, MotorConfig] = Field(default_factory=lambda: dict(DEFAULT_MOTORS))
    use_degrees: bool = Field(default=True, description="Interpret positions as degrees (vs normalised -100..100)")
    disable_torque_on_disconnect: bool = True
    calibration_dir: Path = Field(default=Path("assets/calibration"))


class MotionConfig(BaseModel):
    """Motion runtime tuning parameters."""

    tick_rate_hz: float = Field(default=50.0, gt=0, description="Control loop frequency")
    default_max_velocity: float = Field(default=120.0, gt=0, description="Degrees per second per joint")
    default_max_acceleration: float = Field(default=600.0, gt=0, description="Degrees per second^2 per joint")


class SafetyConfig(BaseModel):
    """Safety kernel limits."""

    joint_limits: dict[str, JointLimits] = Field(default_factory=lambda: dict(DEFAULT_JOINT_LIMITS))
    max_velocity: float = Field(default=180.0, gt=0, description="Hard velocity cap (deg/s)")
    max_acceleration: float = Field(default=900.0, gt=0, description="Hard acceleration cap (deg/s^2)")


class LEDConfig(BaseModel):
    """LED controller settings."""

    port: str = Field(default="", description="Serial port for ESP32")
    baud_rate: int = 9600


class LampgoConfig(BaseModel):
    """Root configuration combining all sub-configs."""

    device: DeviceConfig
    motion: MotionConfig = Field(default_factory=MotionConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    led: LEDConfig = Field(default_factory=LEDConfig)
    recordings_dir: Path = Field(default=Path("assets/recordings"))
