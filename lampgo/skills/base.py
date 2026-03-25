"""Skill base class and context — the only way to move the robot."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from lampgo.core.events import EventBus
from lampgo.core.led import LEDController
from lampgo.core.motion import MotionRuntime
from lampgo.core.types import JointState, SkillResult


@dataclass
class ParameterSpec:
    """Describes a single skill parameter for OpenClaw exposure."""

    name: str
    type: str  # "float", "int", "str", "bool"
    description: str = ""
    required: bool = True
    default: Any = None


@dataclass
class SkillContext:
    """Injected into every skill — provides safe access to subsystems.

    Skills never touch the HAL directly.
    """

    motion: MotionRuntime
    led: LEDController
    events: EventBus
    state: JointState


class Skill(ABC):
    """Base class for all lampgo skills."""

    skill_id: str = ""
    description: str = ""
    parameters: dict[str, ParameterSpec] = {}
    priority: int = 0  # 0 = normal, higher = higher priority

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.skill_id and cls.__name__ != "Skill":
            cls.skill_id = cls.__name__.lower().removesuffix("skill")

    @abstractmethod
    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        ...

    async def cancel(self) -> None:
        """Called when this skill is being pre-empted. Override to clean up."""

    async def rollback(self) -> None:
        """Called after cancellation if the skill needs to undo side-effects."""
