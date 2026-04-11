"""Parametric motion primitives — nod, dance, wave, look_at, idle_sway.

All rhythmic/oscillatory skills use the pre-computed-frames architecture:

    1. Planning layer: generate a complete frame sequence in one shot
    2. Control layer: stream_frames() plays frames at fixed FPS without re-planning

This eliminates the "micro-start/stop" stutter caused by high-frequency
update_target() calls or back-to-back move_to() calls.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import structlog

from lampgo.core.config import DEFAULT_JOINT_LIMITS
from lampgo.core.trajectory import generate_sine_frames, generate_waypoint_frames
from lampgo.core.types import MotionTarget, SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext

logger = structlog.get_logger(__name__)

_FPS = 50  # matches MotionRuntime's default control rate


async def _await_done(done_event, timeout: float) -> bool:
    """Poll a threading.Event from asyncio until it's set or timeout expires."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not done_event.is_set():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.02)
    return True


class NodSkill(Skill):
    """Nod up and down using pre-computed waypoint frames.

    Generates a complete trajectory (down → micro-rebound → … → return)
    in one shot, then streams it.  ``ease_out_back`` interpolation gives
    the organic bouncy feel without discrete re-planning steps.
    """

    skill_id = "nod"
    description = "Nod up and down (agreement gesture)."
    parameters = {
        "amplitude": ParameterSpec(
            name="amplitude", type="float", required=False, default=12.0, description="Degrees"
        ),
        "speed": ParameterSpec(
            name="speed", type="float", required=False, default=55.0, description="Deg/s"
        ),
        "count": ParameterSpec(
            name="count", type="int", required=False, default=3, description="Number of nods"
        ),
    }

    _motion = None

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._motion = ctx.motion
        amplitude = float(params.get("amplitude", 12.0))
        speed = float(params.get("speed", 55.0))
        count = int(params.get("count", 3))
        base_pitch = ctx.state.get("base_pitch", 0.0)

        # Approximate segment duration from amplitude + speed (safety extension in generator)
        seg_dur = max(0.04, amplitude / max(speed, 1.0))

        waypoints: list[tuple[dict[str, float], float]] = [
            ({"base_pitch": base_pitch}, 0.0),  # start
        ]
        for _ in range(count):
            # Dip down
            waypoints.append(({"base_pitch": base_pitch - amplitude}, seg_dur))
            # Micro rebound (≈ 30 % upward overshoot for bouncy feel)
            waypoints.append(({"base_pitch": base_pitch + amplitude * 0.3}, seg_dur * 0.5))
        # Return to centre
        waypoints.append(({"base_pitch": base_pitch}, seg_dur))

        frames = generate_waypoint_frames(
            waypoints,
            fps=_FPS,
            ease_fn="ease_out_back",
            ease_overshoot=0.10,
        )

        try:
            done = ctx.motion.stream_frames(frames, fps=_FPS)
            timeout = len(frames) / _FPS + 3.0
            if not await _await_done(done, timeout=timeout):
                logger.warning("nod.timeout", frames=len(frames))
                return SkillResult(status="error", message="Nod timed out")
        finally:
            self._motion = None

        return SkillResult(status="ok", data={"count": count})

    async def cancel(self) -> None:
        if self._motion is not None:
            self._motion.stop_immediate()


class HeadShakeSkill(Skill):
    """Shake head side to side using a pre-computed sinusoidal frame sequence.

    A pure sine wave on ``base_yaw`` is generated up front so the control
    thread plays it as one continuous stream with no mid-motion re-planning.
    """

    skill_id = "headshake"
    description = "Shake head side to side (disagreement gesture)."
    parameters = {
        "amplitude": ParameterSpec(
            name="amplitude", type="float", required=False, default=15.0, description="Degrees"
        ),
        "speed": ParameterSpec(
            name="speed", type="float", required=False, default=65.0, description="Deg/s"
        ),
        "count": ParameterSpec(
            name="count", type="int", required=False, default=3, description="Number of shakes"
        ),
    }

    _motion = None

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._motion = ctx.motion
        amplitude = float(params.get("amplitude", 15.0))
        speed = float(params.get("speed", 65.0))
        count = int(params.get("count", 3))
        base_yaw = ctx.state.get("base_yaw", 0.0)

        # Period from peak-velocity spec: 2π·A / T = speed → T = 2π·A / speed
        period = (2.0 * math.pi * amplitude) / max(speed, 1.0)
        duration = count * period

        frames = generate_sine_frames(
            base={"base_yaw": base_yaw},
            axes={"base_yaw": {"amplitude": amplitude, "period": period, "phase": 0.0}},
            duration=duration,
            fps=_FPS,
        )

        try:
            done = ctx.motion.stream_frames(frames, fps=_FPS)
            timeout = duration + 3.0
            if not await _await_done(done, timeout=timeout):
                logger.warning("headshake.timeout", frames=len(frames))
                return SkillResult(status="error", message="HeadShake timed out")
        finally:
            self._motion = None

        return SkillResult(status="ok", data={"count": count})

    async def cancel(self) -> None:
        if self._motion is not None:
            self._motion.stop_immediate()


class LookAtSkill(Skill):
    skill_id = "look_at"
    description = (
        "Look in a direction by setting absolute yaw and pitch angles. "
        "Positive pitch = tilt forward/look down; negative pitch = tilt backward/look up. "
        "Positive yaw = turn right; negative yaw = turn left."
    )
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
            description=(
                "Absolute pitch in degrees (-100~65). Positive=forward/look down, "
                "negative=backward/look up. E.g. -60 to look up, 30 to look down. "
                "Omit to keep current pitch."
            ),
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
        data: dict[str, Any] = {
            "yaw": yaw,
            "pitch": pitch,
            "actual_yaw": actual_yaw,
            "actual_pitch": actual_pitch,
        }
        if ctx.motion.status.stalled:
            data["stalled"] = True
            data["warning"] = (
                f"Could not reach target (actual yaw={actual_yaw}, pitch={actual_pitch}). "
                "Do NOT retry the same target."
            )
        return SkillResult(status="ok", data=data)


class IdleSwaySkill(Skill):
    """Gentle idle sway using a pre-computed dual-axis sinusoidal frame sequence.

    Both ``base_pitch`` and ``base_yaw`` are oscillated at slightly different
    frequencies (ratio 1 : 0.7) to produce a natural Lissajous-like organic
    sway pattern.  The entire trajectory is generated once and streamed, so
    the control thread never needs to re-plan mid-motion.
    """

    skill_id = "idle_sway"
    description = "Gentle idle swaying motion (breathing/alive feel)."
    parameters = {
        "amplitude": ParameterSpec(
            name="amplitude", type="float", required=False, default=5.0, description="Degrees"
        ),
        "period": ParameterSpec(
            name="period", type="float", required=False, default=4.0, description="Seconds per cycle"
        ),
        "duration": ParameterSpec(
            name="duration", type="float", required=False, default=20.0, description="Total seconds"
        ),
    }

    _motion = None

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._motion = ctx.motion
        amplitude = float(params.get("amplitude", 5.0))
        period = float(params.get("period", 4.0))
        duration = float(params.get("duration", 20.0))

        base_pitch = ctx.state.get("base_pitch", 0.0)
        base_yaw = ctx.state.get("base_yaw", 0.0)

        frames = generate_sine_frames(
            base={"base_pitch": base_pitch, "base_yaw": base_yaw},
            axes={
                "base_pitch": {"amplitude": amplitude, "period": period, "phase": 0.0},
                "base_yaw": {
                    "amplitude": amplitude * 0.3,
                    "period": period / 0.7,  # slightly different frequency for Lissajous feel
                    "phase": 0.0,
                },
            },
            duration=duration,
            fps=_FPS,
        )

        try:
            done = ctx.motion.stream_frames(frames, fps=_FPS)
            if not await _await_done(done, timeout=duration + 5.0):
                logger.warning("idle_sway.timeout", frames=len(frames))
                return SkillResult(status="error", message="IdleSway timed out")
        finally:
            self._motion = None

        return SkillResult(status="ok")

    async def cancel(self) -> None:
        if self._motion is not None:
            self._motion.stop_immediate()


class DanceSkill(Skill):
    """Rhythmic dance using pre-computed waypoint frames across three axes.

    All joint movements within a cycle are baked into a single frame sequence
    so the control thread streams them without replanning.
    ``ease_out_back`` keeps the bouncy character of the original implementation.
    """

    skill_id = "dance"
    description = "A simple rhythmic dance pattern."
    parameters = {
        "speed": ParameterSpec(
            name="speed", type="float", required=False, default=70.0, description="Deg/s"
        ),
        "cycles": ParameterSpec(
            name="cycles", type="int", required=False, default=4, description="Dance cycles"
        ),
    }

    # Dance offsets per step [yaw_off, pitch_off, roll_off]
    _STEPS = [(20, -10, 15), (-20, -10, -15), (0, 5, 0)]
    # Per-joint max absolute offset including ease_out_back overshoot headroom
    _OVERSHOOT_FACTOR = 1.15
    _MAX_ABS_OFFSETS = {"base_yaw": 20.0, "base_pitch": 10.0, "wrist_roll": 15.0}

    _motion = None

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._motion = ctx.motion
        speed = float(params.get("speed", 70.0))
        cycles = int(params.get("cycles", 4))
        raw_base = {j: ctx.state.get(j, 0.0) for j in ["base_yaw", "base_pitch", "wrist_roll"]}

        # Clamp base position so offsets stay within joint limits
        base: dict[str, float] = {}
        for j, cur in raw_base.items():
            limits = DEFAULT_JOINT_LIMITS.get(j)
            if limits is None:
                base[j] = cur
                continue
            headroom = self._MAX_ABS_OFFSETS.get(j, 0.0) * self._OVERSHOOT_FACTOR
            safe_min = limits.min + headroom
            safe_max = limits.max - headroom
            base[j] = max(safe_min, min(safe_max, cur))

        max_offset = max(self._MAX_ABS_OFFSETS.values())
        seg_dur = max(0.06, max_offset / max(speed, 1.0))

        waypoints: list[tuple[dict[str, float], float]] = [
            (dict(base), 0.0),  # start
        ]
        for _ in range(cycles):
            for yaw_off, pitch_off, roll_off in self._STEPS:
                waypoints.append((
                    {
                        "base_yaw": base["base_yaw"] + yaw_off,
                        "base_pitch": base["base_pitch"] + pitch_off,
                        "wrist_roll": base["wrist_roll"] + roll_off,
                    },
                    seg_dur,
                ))
        waypoints.append((dict(base), seg_dur))

        frames = generate_waypoint_frames(
            waypoints,
            fps=_FPS,
            ease_fn="ease_out_back",
            ease_overshoot=0.10,
        )

        try:
            done = ctx.motion.stream_frames(frames, fps=_FPS)
            timeout = len(frames) / _FPS + 5.0
            if not await _await_done(done, timeout=timeout):
                logger.warning("dance.timeout", frames=len(frames))
                return SkillResult(status="error", message="Dance timed out")
        finally:
            self._motion = None

        return SkillResult(status="ok", data={"cycles": cycles})

    async def cancel(self) -> None:
        if self._motion is not None:
            self._motion.stop_immediate()
