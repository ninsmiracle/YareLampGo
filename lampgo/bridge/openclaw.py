"""OpenClaw adapter — exposes lampgo skills as OpenClaw-callable capabilities."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from lampgo.core.types import InvokeResult
from lampgo.skills.base import Skill, SkillContext
from lampgo.skills.executor import SkillExecutor
from lampgo.skills.registry import SkillRegistry

logger = structlog.get_logger(__name__)


class CapabilitySpec:
    """Describes one callable capability for external agents."""

    def __init__(self, skill: Skill) -> None:
        self.skill_id = skill.skill_id
        self.description = skill.description
        self.parameters = {
            name: {
                "type": spec.type,
                "description": spec.description,
                "required": spec.required,
                "default": spec.default,
            }
            for name, spec in skill.parameters.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "description": self.description,
            "parameters": self.parameters,
        }


class OpenClawAdapter:
    """Bridge between OpenClaw protocol and lampgo's skill system."""

    def __init__(self, registry: SkillRegistry, executor: SkillExecutor) -> None:
        self._registry = registry
        self._executor = executor
        self._event_subscribers: list[Callable[..., Awaitable[None]]] = []

    def get_capabilities(self) -> list[CapabilitySpec]:
        return [CapabilitySpec(skill) for skill in self._registry.list_skills()]

    async def invoke(self, skill_id: str, params: dict, ctx: SkillContext) -> InvokeResult:
        logger.info("openclaw.invoke", skill_id=skill_id, params=params)
        return await self._executor.invoke(skill_id, ctx, **params)

    async def cancel(self, invocation_id: str) -> None:
        logger.info("openclaw.cancel", invocation_id=invocation_id)
        await self._executor.cancel_current()

    def subscribe_events(self, callback: Callable[..., Awaitable[None]]) -> None:
        self._event_subscribers.append(callback)

    def list_capabilities_dict(self) -> list[dict[str, Any]]:
        return [cap.to_dict() for cap in self.get_capabilities()]
