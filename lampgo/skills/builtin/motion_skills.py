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
RECOVERY_HOME_VELOCITY = 30.0
RECOVERY_HOME_FPS = 50
RECOVERY_FINAL_TOLERANCE_DEGREES = 5.0


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

    def __init__(self) -> None:
        self._motion = None
        self._recovery_active = False

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._motion = ctx.motion
        velocity = float(params.get("velocity", 60.0))
        safe = get_safe_position()

        if bool(getattr(ctx.motion, "recovery_required", False)):
            self._recovery_active = True
            logger.info(
                "return_safe.recovery_preflight",
                requested_velocity=velocity,
                recovery_velocity=RECOVERY_HOME_VELOCITY,
                target=safe,
            )
            try:
                frames = await asyncio.to_thread(
                    ctx.motion.prepare_recovery,
                    safe,
                    max_velocity=RECOVERY_HOME_VELOCITY,
                    fps=RECOVERY_HOME_FPS,
                )
                done_event = ctx.motion.stream_recovery_frames(frames, fps=RECOVERY_HOME_FPS)
                # Do not infer failure from the precomputed frame duration.
                # Under gravity a healthy loaded joint can trail the command
                # timeline while still moving steadily toward the safe target.
                # MotionRuntime signals completion only after real feedback is
                # at target, or after its safety watchdog has already stopped a
                # genuine fault.
                while not done_event.is_set():
                    await asyncio.sleep(0.05)

                recovery_error = getattr(ctx.motion, "recovery_error", None)
                if recovery_error:
                    raise RuntimeError(str(recovery_error))

                actual = ctx.motion.current_state.positions
                errors = {
                    joint: round(actual.get(joint, float("inf")) - target, 2)
                    for joint, target in safe.items()
                    if abs(actual.get(joint, float("inf")) - target) > RECOVERY_FINAL_TOLERANCE_DEGREES
                }
                if errors:
                    raise RuntimeError(
                        f"Recovery did not reach the verified return_safe target; joint errors: {errors}"
                    )

                await asyncio.to_thread(ctx.motion.complete_recovery)
                self._recovery_active = False
            except Exception as exc:  # noqa: BLE001
                # Recovery failures are reported without automatically
                # releasing torque. The motor bus already enforces its torque
                # limit, and ordinary software observations (lag, clipping or
                # a completion mismatch) must not make a raised arm fall.
                logger.warning(
                    "return_safe.recovery_failed_holding_torque",
                    error=str(exc),
                    torque_held=True,
                )
                self._recovery_active = False
                return SkillResult(status="error", message=str(exc))

            self._recovery_active = False
            logger.info(
                "return_safe.recovery_done",
                velocity=RECOVERY_HOME_VELOCITY,
                frame_count=len(frames),
            )
            return SkillResult(
                status="ok",
                data={
                    "recovered": True,
                    "velocity": RECOVERY_HOME_VELOCITY,
                    "frame_count": len(frames),
                },
            )

        target = MotionTarget(joints=dict(safe), max_velocity=velocity, anticipation=False)
        logger.info("return_safe.queueing", velocity=velocity, target=safe)
        done_event = ctx.motion.move_to(target)
        logger.info("return_safe.awaiting_done")
        if not await _await_done(done_event, timeout=60.0):
            logger.warning("return_safe.timeout")
            return SkillResult(status="error", message="Homing did not complete within timeout")
        logger.info("return_safe.done")
        return SkillResult(status="ok")

    async def cancel(self) -> None:
        if self._motion is None:
            return
        if self._recovery_active:
            # Cancellation/pre-emption is not an emergency stop. Keep the
            # active servo goals and torque so the structure cannot fall.
            self._motion.stop_immediate()
            self._recovery_active = False
            return
        self._motion.stop_immediate()


class EStopSkill(Skill):
    skill_id = "estop"
    description = "Emergency stop — immediately halt all motion."
    priority = 100

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        ctx.motion.stop_immediate()
        return SkillResult(status="ok", message="Emergency stop activated")
