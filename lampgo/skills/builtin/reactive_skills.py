"""Reactive skills — triggered by perception events rather than direct invocation.

M2 introduces background/foreground distinction:
  - Reactive skills run in the background and yield to foreground user-invoked skills.
  - They can be interrupted at any time without waiting.

Motion API note
---------------
* Real-time reactive control (visual servo, face-follow): use ``update_target(style="linear")``
  to inject individual targets as new sensor data arrives.  This avoids trajectory re-planning
  latency and is appropriate when updates are truly data-driven (not time-driven).
* Stub/scanning patterns: use ``generate_sine_frames`` + ``stream_frames`` so the control
  thread plays a smooth pre-computed trajectory without per-update re-planning.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from lampgo.core.trajectory import generate_sine_frames
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


_FPS = 50  # matches MotionRuntime control rate


async def _await_done(done_event, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not done_event.is_set():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.02)
    return True


class FaceFollowSkill(Skill):
    """Scan-pattern stub for face-following.

    The real implementation would read a face bounding-box from a perception
    module and call ``update_target(style="linear")`` with the computed angle
    deltas on each sensor tick (true reactive / visual-servo control).

    This stub uses ``generate_sine_frames`` + ``stream_frames`` so the scanning
    motion is smooth and continuous without any per-update trajectory re-planning.
    Replace the frame generation with real sensor-driven updates when the
    perception module is available.
    """

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

    _motion = None

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._motion = ctx.motion
        duration = float(params.get("duration", 30.0))
        speed = float(params.get("speed", 40.0))

        base_yaw = ctx.state.get("base_yaw", 0.0)
        base_pitch = ctx.state.get("base_pitch", 0.0)

        # Period derived from peak-velocity spec: T = 2π·A / speed
        yaw_amp = 15.0
        pitch_amp = 5.0
        yaw_period = (2.0 * 3.14159265 * yaw_amp) / max(speed, 1.0)

        frames = generate_sine_frames(
            base={"base_yaw": base_yaw, "base_pitch": base_pitch},
            axes={
                "base_yaw": {"amplitude": yaw_amp, "period": yaw_period, "phase": 0.0},
                "base_pitch": {"amplitude": pitch_amp, "period": yaw_period / 1.3, "phase": 0.0},
            },
            duration=duration,
            fps=_FPS,
        )

        try:
            done = ctx.motion.stream_frames(frames, fps=_FPS)
            completed = await _await_done(done, timeout=duration + 5.0)
        finally:
            self._motion = None

        return SkillResult(status="ok" if completed else "cancelled")

    async def cancel(self) -> None:
        if self._motion is not None:
            self._motion.stop_immediate()
