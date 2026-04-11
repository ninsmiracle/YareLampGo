"""Foundation types shared across all lampgo modules."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

JOINT_NAMES: list[str] = [
    "base_yaw",
    "base_pitch",
    "elbow_pitch",
    "wrist_roll",
    "wrist_pitch",
]


@dataclass(frozen=True)
class JointState:
    """Immutable snapshot of all joint positions at a point in time."""

    positions: dict[str, float]
    timestamp: float = field(default_factory=time.monotonic)

    def get(self, joint: str, default: float = 0.0) -> float:
        return self.positions.get(joint, default)


@dataclass
class MotionTarget:
    """Desired target for the motion runtime.

    ``joints`` may be partial — only the specified joints will move,
    the rest hold their current positions.
    """

    joints: dict[str, float]
    max_velocity: float | None = None
    max_acceleration: float | None = None
    style: str | None = None


@dataclass
class MotionStatus:
    """Read-only status exposed by the motion runtime."""

    target: MotionTarget | None = None
    progress: float = 0.0
    is_done: bool = True
    stalled: bool = False


class DeviceHealth(Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True)
class SafetyRejection:
    """Returned by SafetyKernel when a target or frame is rejected."""

    reason: str
    joint: str | None = None
    value: float | None = None
    limit: float | None = None


class DeviceState(Enum):
    """Top-level FSM states."""

    IDLE = "idle"
    EXECUTING = "executing"
    SAFE_STOP = "safe_stop"
    RECOVERING = "recovering"
    TELEOP = "teleop"


@dataclass
class SkillResult:
    """Returned by a skill after execution completes."""

    status: Literal["ok", "cancelled", "error"]
    message: str = ""
    data: dict | None = None


@dataclass
class InvokeResult:
    """Returned to external callers (OpenClaw / CLI) after skill invocation."""

    invocation_id: str
    status: Literal["ok", "rejected", "cancelled", "error"]
    error_code: str | None = None
    error_detail: str | None = None
    result: dict | None = None
