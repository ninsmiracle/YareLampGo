"""SkillRegistry — register, look up, and list available skills."""

from __future__ import annotations

import structlog

from lampgo.skills.base import Skill

logger = structlog.get_logger(__name__)


class SkillRegistry:
    """Central registry of all available skills.

    Skills register themselves (or are registered at startup).
    The OpenClaw adapter reads this registry to expose capabilities.
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.skill_id in self._skills:
            logger.warning("registry.duplicate", skill_id=skill.skill_id)
        self._skills[skill.skill_id] = skill
        logger.info("registry.registered", skill_id=skill.skill_id)

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def list_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def list_ids(self) -> list[str]:
        return list(self._skills.keys())

    def __contains__(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def __len__(self) -> int:
        return len(self._skills)
