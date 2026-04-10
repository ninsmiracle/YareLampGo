"""Parametric motion primitives — nod, dance, wave, look_at, idle_sway."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from lampgo.core.config import DEFAULT_JOINT_LIMITS
from lampgo.core.types import MotionTarget, SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext


class NodSkill(Skill):
    skill_id = "nod"
    description = "Nod up and down (agreement gesture)."
    parameters = {
        "amplitude": ParameterSpec(name="amplitude", type="float", required=False, default=15.0, description="Degrees"),
        "speed": ParameterSpec(name="speed", type="float", required=False, default=80.0, description="Deg/s"),
        "count": ParameterSpec(name="count", type="int", required=False, default=3, description="Number of nods"),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        amplitude = float(params.get("amplitude", 15.0))
        speed = float(params.get("speed", 80.0))
        count = int(params.get("count", 3))
        base = ctx.state.get("base_pitch", 0.0)

        for _ in range(count):
            done = ctx.motion.move_to(
                MotionTarget(joints={"base_pitch": base - amplitude}, max_velocity=speed, style="bouncy")
            )
            while not done.is_set():
                await asyncio.sleep(0.03)
            done = ctx.motion.move_to(
                MotionTarget(joints={"base_pitch": base + amplitude * 0.3}, max_velocity=speed, style="bouncy")
            )
            while not done.is_set():
                await asyncio.sleep(0.03)

        done = ctx.motion.move_to(MotionTarget(joints={"base_pitch": base}, max_velocity=speed, style="bouncy"))
        while not done.is_set():
            await asyncio.sleep(0.03)

        return SkillResult(status="ok", data={"count": count})


class HeadShakeSkill(Skill):
    skill_id = "headshake"
    description = "Shake head side to side (disagreement gesture)."
    parameters = {
        "amplitude": ParameterSpec(name="amplitude", type="float", required=False, default=20.0, description="Degrees"),
        "speed": ParameterSpec(name="speed", type="float", required=False, default=100.0, description="Deg/s"),
        "count": ParameterSpec(name="count", type="int", required=False, default=3, description="Number of shakes"),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        amplitude = float(params.get("amplitude", 20.0))
        speed = float(params.get("speed", 100.0))
        count = int(params.get("count", 3))
        base = ctx.state.get("base_yaw", 0.0)

        for _ in range(count):
            done = ctx.motion.move_to(MotionTarget(joints={"base_yaw": base - amplitude}, max_velocity=speed))
            while not done.is_set():
                await asyncio.sleep(0.03)
            done = ctx.motion.move_to(MotionTarget(joints={"base_yaw": base + amplitude}, max_velocity=speed))
            while not done.is_set():
                await asyncio.sleep(0.03)

        done = ctx.motion.move_to(MotionTarget(joints={"base_yaw": base}, max_velocity=speed))
        while not done.is_set():
            await asyncio.sleep(0.03)

        return SkillResult(status="ok", data={"count": count})


class LookAtSkill(Skill):
    skill_id = "look_at"
    description = "Look in a direction by setting absolute yaw and pitch angles. Positive pitch = tilt forward/look down; negative pitch = tilt backward/look up. Positive yaw = turn right; negative yaw = turn left."
    parameters = {
        "yaw": ParameterSpec(
            name="yaw",
            type="float",
            required=False,
            default=None,
            description="Absolute yaw in degrees (-150~150). Positive=right, negative=left. Omit to keep current yaw.",
        ),
        "pitch": ParameterSpec(
            name="pitch",
            type="float",
            required=False,
            default=None,
            description="Absolute pitch in degrees (-100~65). Positive=forward/look down, negative=backward/look up. E.g. -60 to look up, 30 to look down. Omit to keep current pitch.",
        ),
        "velocity": ParameterSpec(name="velocity", type="float", required=False, description="Max deg/s"),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        yaw_value = params.get("yaw")
        pitch_value = params.get("pitch")
        yaw = float(yaw_value) if yaw_value is not None else ctx.state.get("base_yaw", 0.0)
        pitch = float(pitch_value) if pitch_value is not None else ctx.state.get("base_pitch", 0.0)
        velocity = params.get("velocity")
        target = MotionTarget(
            joints={"base_yaw": yaw, "base_pitch": pitch},
            max_velocity=float(velocity) if velocity is not None else None,
            style="confident",
        )
        done = ctx.motion.move_to(target)
        while not done.is_set():
            await asyncio.sleep(0.03)

        actual_yaw = round(ctx.state.get("base_yaw", yaw), 1)
        actual_pitch = round(ctx.state.get("base_pitch", pitch), 1)
        data: dict[str, Any] = {"yaw": yaw, "pitch": pitch, "actual_yaw": actual_yaw, "actual_pitch": actual_pitch}
        if ctx.motion.status.stalled:
            data["stalled"] = True
            data["warning"] = f"Could not reach target (actual yaw={actual_yaw}, pitch={actual_pitch}). Do NOT retry the same target."
        return SkillResult(status="ok", data=data)


class IdleSwaySkill(Skill):
    skill_id = "idle_sway"
    description = "Gentle idle swaying motion (breathing/alive feel)."
    parameters = {
        "amplitude": ParameterSpec(name="amplitude", type="float", required=False, default=5.0, description="Degrees"),
        "period": ParameterSpec(
            name="period", type="float", required=False, default=4.0, description="Seconds per cycle"
        ),
        "duration": ParameterSpec(
            name="duration", type="float", required=False, default=20.0, description="Total seconds"
        ),
    }
    _cancelled = False

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._cancelled = False
        amplitude = float(params.get("amplitude", 5.0))
        period = float(params.get("period", 4.0))
        duration = float(params.get("duration", 20.0))
        base_pitch = ctx.state.get("base_pitch", 0.0)
        base_yaw = ctx.state.get("base_yaw", 0.0)

        elapsed = 0.0
        step = 0.1
        while elapsed < duration and not self._cancelled:
            t = elapsed / period * 2 * math.pi
            pitch_offset = amplitude * math.sin(t)
            yaw_offset = amplitude * 0.3 * math.sin(t * 0.7)
            ctx.motion.update_target(
                MotionTarget(
                    joints={
                        "base_pitch": base_pitch + pitch_offset,
                        "base_yaw": base_yaw + yaw_offset,
                    },
                    max_velocity=30.0,
                )
            )
            await asyncio.sleep(step)
            elapsed += step

        ctx.motion.update_target(MotionTarget(joints={"base_pitch": base_pitch, "base_yaw": base_yaw}))
        return SkillResult(status="ok" if not self._cancelled else "cancelled")

    async def cancel(self) -> None:
        self._cancelled = True


class DanceSkill(Skill):
    skill_id = "dance"
    description = "A simple rhythmic dance pattern."
    parameters = {
        "speed": ParameterSpec(name="speed", type="float", required=False, default=120.0, description="Deg/s"),
        "cycles": ParameterSpec(name="cycles", type="int", required=False, default=4, description="Dance cycles"),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        speed = float(params.get("speed", 120.0))
        cycles = int(params.get("cycles", 4))
        raw_base = {j: ctx.state.get(j, 0.0) for j in ["base_yaw", "base_pitch", "wrist_roll"]}

        # Dance pattern offsets — keep in sync with the loop below.
        # Per-joint maximum absolute offset used across all steps, plus a small
        # headroom factor that accounts for ease_out_back overshoot (~10%).
        _OVERSHOOT_FACTOR = 1.15
        _MAX_ABS_OFFSETS = {"base_yaw": 20.0, "base_pitch": 10.0, "wrist_roll": 15.0}
        base: dict[str, float] = {}
        for j, cur in raw_base.items():
            limits = DEFAULT_JOINT_LIMITS.get(j)
            if limits is None:
                base[j] = cur
                continue
            headroom = _MAX_ABS_OFFSETS.get(j, 0.0) * _OVERSHOOT_FACTOR
            safe_min = limits.min + headroom
            safe_max = limits.max - headroom
            base[j] = max(safe_min, min(safe_max, cur))

        for _ in range(cycles):
            for yaw_off, pitch_off, roll_off in [(20, -10, 15), (-20, -10, -15), (0, 5, 0)]:
                done = ctx.motion.move_to(
                    MotionTarget(
                        joints={
                            "base_yaw": base["base_yaw"] + yaw_off,
                            "base_pitch": base["base_pitch"] + pitch_off,
                            "wrist_roll": base["wrist_roll"] + roll_off,
                        },
                        max_velocity=speed,
                        style="bouncy",
                    )
                )
                while not done.is_set():
                    await asyncio.sleep(0.03)

        done = ctx.motion.move_to(MotionTarget(joints=dict(base), max_velocity=speed, style="bouncy"))
        while not done.is_set():
            await asyncio.sleep(0.03)

        return SkillResult(status="ok", data={"cycles": cycles})
