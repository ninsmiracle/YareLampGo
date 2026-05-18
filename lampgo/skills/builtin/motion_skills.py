"""Built-in motion skills: move_to, return_safe, estop."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from lampgo.core.types import MotionTarget, SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext

logger = structlog.get_logger(__name__)

SAFE_POSITION: dict[str, float] = {
    "base_yaw": 0.0,
    "base_pitch": 0.0,
    "elbow_pitch": 0.0,
    "wrist_roll": 0.0,
    "wrist_pitch": 0.0,
}

def set_calibration_home(home: dict[str, float]) -> None:
    """Inject calibration-derived home for return_safe/startup homing."""
    for joint in SAFE_POSITION:
        if joint in home:
            SAFE_POSITION[joint] = float(home[joint])
    logger.info("motion.safe_position_updated_from_calibration", safe_position=SAFE_POSITION)


def get_safe_position() -> dict[str, float]:
    return dict(SAFE_POSITION)


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

        current = ctx.motion.current_state
        actual = {k: round(current.get(k, 0.0), 1) for k in joints}
        data: dict[str, Any] = {"target": joints, "actual": actual}
        if ctx.motion.status.stalled:
            data["stalled"] = True
            data["warning"] = "Some joints could not reach their target (physically blocked or out of range)."
        return SkillResult(status="ok", data=data)


STARTUP_HOME_VELOCITY = 30.0


class ReturnSafeSkill(Skill):
    skill_id = "return_safe"
    description = "Smoothly return to the fixed idle pose safe position."
    priority = 90
    parameters = {
        "velocity": ParameterSpec(
            name="velocity",
            type="float",
            required=False,
            default=60.0,
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
