"""Reactive skills — triggered by perception events rather than direct invocation.

M2 introduces background/foreground distinction:
  - Reactive skills run in the background and yield to foreground user-invoked skills.
  - They can be interrupted at any time without waiting.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import structlog

from lampgo.core.types import MotionTarget, SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext

logger = structlog.get_logger(__name__)


class PresenceReactSkill(Skill):
    skill_id = "presence_react"
    description = "React when a person is detected — look toward them and show a greeting expression."
    priority = -10  # background, lower than normal

    _cancelled = False

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._cancelled = False

        ctx.led.set_mode("smiley")
        done = ctx.motion.move_to(MotionTarget(joints={"base_pitch": 10.0, "base_yaw": 0.0}, max_velocity=60.0))
        while not done.is_set() and not self._cancelled:
            await asyncio.sleep(0.05)

        if self._cancelled:
            return SkillResult(status="cancelled")

        # Hold greeting pose briefly
        await asyncio.sleep(1.5)

        # Small nod acknowledgment
        base_pitch = ctx.state.get("base_pitch", 0.0)
        for _ in range(2):
            if self._cancelled:
                return SkillResult(status="cancelled")
            done = ctx.motion.move_to(MotionTarget(joints={"base_pitch": base_pitch - 10}, max_velocity=80.0))
            while not done.is_set() and not self._cancelled:
                await asyncio.sleep(0.03)
            done = ctx.motion.move_to(MotionTarget(joints={"base_pitch": base_pitch}, max_velocity=80.0))
            while not done.is_set() and not self._cancelled:
                await asyncio.sleep(0.03)

        return SkillResult(status="ok")

    async def cancel(self) -> None:
        self._cancelled = True


class FaceFollowSkill(Skill):
    skill_id = "face_follow"
    description = "Continuously track a detected face by adjusting yaw and pitch."
    priority = -10  # background
    parameters = {
        "duration": ParameterSpec(
            name="duration", type="float", required=False, default=30.0, description="Tracking duration (seconds)"
        ),
        "speed": ParameterSpec(
            name="speed", type="float", required=False, default=40.0, description="Max tracking velocity"
        ),
    }

    _cancelled = False

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        """Stub face-follow that does a slow scanning pattern.

        Real implementation would read face bounding box from a perception
        module and convert pixel offset to joint angle deltas.
        """
        self._cancelled = False
        duration = float(params.get("duration", 30.0))
        speed = float(params.get("speed", 40.0))

        base_yaw = ctx.state.get("base_yaw", 0.0)
        base_pitch = ctx.state.get("base_pitch", 0.0)
        elapsed = 0.0
        step = 0.15

        while elapsed < duration and not self._cancelled:
            t = elapsed * 0.5
            yaw = base_yaw + 15.0 * math.sin(t)
            pitch = base_pitch + 5.0 * math.sin(t * 1.3)
            ctx.motion.update_target(
                MotionTarget(joints={"base_yaw": yaw, "base_pitch": pitch}, max_velocity=speed)
            )
            await asyncio.sleep(step)
            elapsed += step

        return SkillResult(status="ok" if not self._cancelled else "cancelled")

    async def cancel(self) -> None:
        self._cancelled = True
