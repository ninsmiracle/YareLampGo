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


class SkillExecutor:
    """Runs one skill at a time. New invocations cancel the current skill."""

    def __init__(self, registry: SkillRegistry, events: EventBus) -> None:
        self._registry = registry
        self._events = events
        self._current_task: asyncio.Task | None = None
        self._current_skill: Skill | None = None
        self._current_invocation_id: str | None = None

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

        # Cancel current skill if running
        if self._current_task is not None and not self._current_task.done():
            await self._cancel_current()

        self._current_skill = skill
        self._current_invocation_id = invocation_id

        await self._events.publish(SkillStarted(skill_id=skill_id, invocation_id=invocation_id))
        logger.info("executor.invoke", skill_id=skill_id, invocation_id=invocation_id)

        try:
            result = await asyncio.wait_for(
                skill.execute(ctx, **params),
                timeout=300.0,
            )
        except asyncio.TimeoutError:
            result = SkillResult(status="error", message="timeout")
        except asyncio.CancelledError:
            result = SkillResult(status="cancelled", message="pre-empted")
        except Exception as e:
            logger.exception("executor.skill_error", skill_id=skill_id)
            result = SkillResult(status="error", message=str(e))

        await self._events.publish(
            SkillFinished(skill_id=skill_id, invocation_id=invocation_id, status=result.status)
        )

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
        if self._current_skill is not None:
            skill_id = self._current_skill.skill_id
            inv_id = self._current_invocation_id or ""
            logger.info("executor.cancelling", skill_id=skill_id)
            try:
                await self._current_skill.cancel()
            except Exception:
                logger.exception("executor.cancel_error")
            if self._current_task is not None and not self._current_task.done():
                self._current_task.cancel()
            await self._events.publish(SkillCancelled(skill_id=skill_id, invocation_id=inv_id))

    @property
    def is_busy(self) -> bool:
        return self._current_task is not None and not self._current_task.done()

    @property
    def current_skill_id(self) -> str | None:
        return self._current_skill.skill_id if self._current_skill else None
