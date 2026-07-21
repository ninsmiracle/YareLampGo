"""SkillExecutor — runs skills with cancel/timeout, enforces scheduling rules.

M1 scheduling: simple last-writer-wins with estop/return_safe as highest priority.
"""

from __future__ import annotations

import asyncio
import uuid

import structlog

from lampgo.core.events import EventBus, SkillCancelled, SkillFinished, SkillStarted
from lampgo.core.types import InvokeResult, SkillResult
from lampgo.skills.base import Skill, SkillContext
from lampgo.skills.registry import SkillRegistry

logger = structlog.get_logger(__name__)

PRIORITY_SKILLS = {"estop", "return_safe"}

_MOTION_SKILLS = {
    "nod", "headshake", "look_at", "idle_sway",
    "dance_to_music", "cat_teaser",
    "move_to", "return_safe", "estop",
    "presence_react", "face_follow",
    "play_recording", "teleop_mouse", "teleop_gamepad",
}
_LED_SKILLS = {
    "set_expression",
    "show_clock",
    "start_electronic_ocean", "stop_electronic_ocean",
    "presence_react",
    "teleop_mouse", "teleop_gamepad",
}


class SkillExecutor:
    """Runs one skill at a time. New invocations cancel the current skill."""

    def __init__(self, registry: SkillRegistry, events: EventBus) -> None:
        self._registry = registry
        self._events = events
        self._lock = asyncio.Lock()
        self._current_task: asyncio.Task | None = None
        self._current_skill: Skill | None = None
        self._current_invocation_id: str | None = None
        self._motion_block_reason: str | None = None
        self._allow_return_safe_recovery = False

    def set_motion_block_reason(
        self,
        reason: str | None,
        *,
        allow_return_safe_recovery: bool = False,
    ) -> None:
        """Block physical motion skills after a failed hardware startup.

        Explicit ``--no-hw`` sessions leave this unset and may still use the
        virtual runtime for simulation. A hardware failure must never be
        presented as a successful virtual movement.
        """
        self._motion_block_reason = reason
        self._allow_return_safe_recovery = bool(reason) and allow_return_safe_recovery

    async def invoke(self, skill_id: str, ctx: SkillContext, **params) -> InvokeResult:
        invocation_id = uuid.uuid4().hex[:12]

        skill = self._registry.get(skill_id)
        if skill is None:
            return InvokeResult(
                invocation_id=invocation_id,
                status="rejected",
                error_code="unknown_skill",
                error_detail=f"Skill '{skill_id}' not registered",
            )

        recovery_exception = self._allow_return_safe_recovery and skill_id in {"return_safe", "estop"}
        if skill_id in _MOTION_SKILLS and self._motion_block_reason and not recovery_exception:
            logger.warning(
                "executor.motion_blocked_hardware_unavailable",
                skill_id=skill_id,
                reason=self._motion_block_reason,
            )
            return InvokeResult(
                invocation_id=invocation_id,
                status="rejected",
                error_code="motor_hardware_unavailable",
                error_detail=self._motion_block_reason,
            )

        # Hardware not connected → return fake ok to prevent LLM retry loops
        if skill_id in _MOTION_SKILLS and not ctx.motion.is_running and not recovery_exception:
            logger.debug("executor.hw_skip", skill_id=skill_id, reason="motor not connected")
            return InvokeResult(
                invocation_id=invocation_id,
                status="ok",
                result={"note": "motor not connected, skipped"},
            )
        if skill_id in _LED_SKILLS and not ctx.led.is_connected:
            logger.debug("executor.hw_skip", skill_id=skill_id, reason="LED not connected")
            return InvokeResult(
                invocation_id=invocation_id,
                status="ok",
                result={"note": "LED not connected, skipped"},
            )
        if (
            skill_id in _LED_SKILLS
            and skill_id not in {"show_clock", "stop_electronic_ocean"}
            and ctx.clock is not None
        ):
            ctx.clock.deactivate()
        if skill_id in _LED_SKILLS and skill_id not in {"start_electronic_ocean", "stop_electronic_ocean"}:
            if ctx.electronic_ocean is not None:
                ctx.electronic_ocean.deactivate()

        async with self._lock:
            # Cancel current skill if running. This gives rapid UI clicks
            # last-writer-wins semantics without leaving old skill coroutines
            # around to run their own cleanup motions later.
            if self._current_task is not None and not self._current_task.done():
                await self._cancel_current_locked()

            self._current_skill = skill
            self._current_invocation_id = invocation_id
            self._current_task = asyncio.create_task(skill.execute(ctx, **params))
            task = self._current_task

            await self._events.publish(SkillStarted(skill_id=skill_id, invocation_id=invocation_id))
            logger.info("executor.invoke", skill_id=skill_id, invocation_id=invocation_id)

        try:
            result = await asyncio.wait_for(task, timeout=300.0)
        except TimeoutError:
            logger.error("executor.timeout", skill_id=skill_id)
            async with self._lock:
                if self._current_task is task:
                    await self._cancel_current_locked()
            result = SkillResult(status="error", message="timeout")
        except asyncio.CancelledError:
            logger.info("executor.cancelled", skill_id=skill_id, invocation_id=invocation_id)
            async with self._lock:
                if self._current_task is task:
                    await self._cancel_current_locked()
            result = SkillResult(status="cancelled", message="pre-empted")
        except Exception as e:
            logger.exception("executor.skill_error", skill_id=skill_id)
            result = SkillResult(status="error", message=str(e))

        if (
            skill_id == "return_safe"
            and result.status == "ok"
            and self._allow_return_safe_recovery
            and not bool(getattr(ctx.motion, "recovery_required", False))
        ):
            self.set_motion_block_reason(None)
            logger.info("executor.motor_recovery_unblocked")

        await self._events.publish(
            SkillFinished(skill_id=skill_id, invocation_id=invocation_id, status=result.status)
        )

        async with self._lock:
            if self._current_task is task:
                self._current_skill = None
                self._current_invocation_id = None
                self._current_task = None

        return InvokeResult(
            invocation_id=invocation_id,
            status=result.status,
            result=result.data,
            error_detail=result.message if result.status != "ok" else None,
        )

    async def cancel_current(self) -> None:
        await self._cancel_current()

    async def _cancel_current(self) -> None:
        async with self._lock:
            await self._cancel_current_locked()

    async def _cancel_current_locked(self) -> None:
        skill = self._current_skill
        task = self._current_task
        inv_id = self._current_invocation_id or ""
        if skill is None and task is None:
            return

        skill_id = skill.skill_id if skill is not None else ""
        if skill is not None:
            logger.info("executor.cancelling", skill_id=skill_id)
            try:
                await skill.cancel()
            except Exception:
                logger.exception("executor.cancel_error")
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("executor.cancelled_task_error", skill_id=skill_id)
        if skill_id:
            await self._events.publish(SkillCancelled(skill_id=skill_id, invocation_id=inv_id))

        if self._current_task is task:
            self._current_skill = None
            self._current_invocation_id = None
            self._current_task = None

    @property
    def is_busy(self) -> bool:
        return self._current_task is not None and not self._current_task.done()

    @property
    def current_skill_id(self) -> str | None:
        return self._current_skill.skill_id if self._current_skill else None
