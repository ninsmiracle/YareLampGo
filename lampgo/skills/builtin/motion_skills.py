"""Built-in motion skills: move_to, return_safe, estop."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from lampgo.core.types import MotionTarget, SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext

logger = structlog.get_logger(__name__)

FALLBACK_SAFE_POSITION: dict[str, float] = {
    "base_yaw": 0.0,
    "base_pitch": 0.0,
    "elbow_pitch": 0.0,
    "wrist_roll": 0.0,
    "wrist_pitch": 0.0,
}

_cached_home: dict[str, float] | None = None


def set_calibration_home(home: dict[str, float]) -> None:
    """Called by server on startup to inject the calibration-derived home position."""
    global _cached_home
    _cached_home = home


def get_safe_position() -> dict[str, float]:
    return _cached_home if _cached_home is not None else FALLBACK_SAFE_POSITION

MOVE_TIMEOUT_S = 15.0


async def _await_done(done_event, timeout: float = MOVE_TIMEOUT_S) -> bool:
    """Poll a threading.Event with an async timeout. Returns True if done, False if timed out."""
    deadline = time.monotonic() + timeout
    while not done_event.is_set():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


class MoveToSkill(Skill):
    skill_id = "move_to"
    description = "Move to a target joint configuration with smooth trapezoidal interpolation."
    parameters = {
        "base_yaw": ParameterSpec(name="base_yaw", type="float", required=False, description="Target yaw (degrees)"),
        "base_pitch": ParameterSpec(name="base_pitch", type="float", required=False, description="Target pitch"),
        "elbow_pitch": ParameterSpec(name="elbow_pitch", type="float", required=False, description="Target elbow"),
        "wrist_roll": ParameterSpec(name="wrist_roll", type="float", required=False, description="Target wrist roll"),
        "wrist_pitch": ParameterSpec(name="wrist_pitch", type="float", required=False, description="Wrist pitch"),
        "velocity": ParameterSpec(name="velocity", type="float", required=False, description="Max velocity deg/s"),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        joints = {k: float(v) for k, v in params.items() if k in get_safe_position() and v is not None}
        if not joints:
            return SkillResult(status="error", message="No joint targets provided")

        velocity = params.get("velocity")
        target = MotionTarget(
            joints=joints,
            max_velocity=float(velocity) if velocity is not None else None,
        )

        done_event = ctx.motion.move_to(target)
        if not await _await_done(done_event):
            logger.warning("move_to.timeout", target=joints)
            return SkillResult(status="error", message="Motion did not complete within timeout")

        return SkillResult(status="ok", data={"target": joints})


STARTUP_HOME_VELOCITY = 30.0


class ReturnSafeSkill(Skill):
    skill_id = "return_safe"
    description = "Smoothly return to safe home position (calibration midpoint = all joints 0 degrees)."
    priority = 90
    parameters = {
        "velocity": ParameterSpec(
            name="velocity", type="float", required=False, default=60.0,
            description="Max velocity deg/s (use lower values for gentler homing)",
        ),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        velocity = float(params.get("velocity", 60.0))
        safe = get_safe_position()
        target = MotionTarget(joints=dict(safe), max_velocity=velocity)
        logger.info("return_safe.queueing", velocity=velocity, target=safe)
        done_event = ctx.motion.move_to(target)
        logger.info("return_safe.awaiting_done")
        if not await _await_done(done_event, timeout=60.0):
            logger.warning("return_safe.timeout")
            return SkillResult(status="error", message="Homing did not complete within timeout")
        logger.info("return_safe.done")
        return SkillResult(status="ok")


class EStopSkill(Skill):
    skill_id = "estop"
    description = "Emergency stop — immediately halt all motion."
    priority = 100

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        ctx.motion.stop_immediate()
        return SkillResult(status="ok", message="Emergency stop activated")
