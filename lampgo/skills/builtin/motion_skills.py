"""Built-in motion skills: move_to, return_safe, estop."""

from __future__ import annotations

import asyncio
from typing import Any

from lampgo.core.types import MotionTarget, SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext

SAFE_POSITION: dict[str, float] = {
    "base_yaw": 0.0,
    "base_pitch": 0.0,
    "elbow_pitch": 0.0,
    "wrist_roll": 0.0,
    "wrist_pitch": 0.0,
}


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
        joints = {k: float(v) for k, v in params.items() if k in SAFE_POSITION and v is not None}
        if not joints:
            return SkillResult(status="error", message="No joint targets provided")

        velocity = params.get("velocity")
        target = MotionTarget(
            joints=joints,
            max_velocity=float(velocity) if velocity is not None else None,
        )

        done_event = ctx.motion.move_to(target)
        while not done_event.is_set():
            await asyncio.sleep(0.05)

        return SkillResult(status="ok", data={"target": joints})


class ReturnSafeSkill(Skill):
    skill_id = "return_safe"
    description = "Smoothly return to safe home position."
    priority = 90

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        target = MotionTarget(joints=dict(SAFE_POSITION))
        done_event = ctx.motion.move_to(target)
        while not done_event.is_set():
            await asyncio.sleep(0.05)
        return SkillResult(status="ok")


class EStopSkill(Skill):
    skill_id = "estop"
    description = "Emergency stop — immediately halt all motion."
    priority = 100

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        ctx.motion.stop_immediate()
        return SkillResult(status="ok", message="Emergency stop activated")
