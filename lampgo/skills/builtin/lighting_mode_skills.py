"""Factory skills for the persistent conventional desk-lamp mode."""

from __future__ import annotations

from typing import Any

from lampgo.core.types import SkillResult
from lampgo.lighting_mode import LightingModeController
from lampgo.skills.base import Skill, SkillContext
from lampgo.skills.builtin.motion_skills import ReturnSafeSkill


class EnterLightingModeSkill(Skill):
    skill_id = "enter_lighting_mode"
    label = "启动照明模式"
    description = (
        "当用户明确要求启动或进入照明模式时调用。机械臂只移动一次到录制的照明姿态，"
        "随后保持不动，并让 S3 LED 白色常亮。"
    )
    parameters = {}

    def __init__(self, controller: LightingModeController) -> None:
        self._controller = controller

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        del ctx, params
        return await self._controller.enter()

    async def cancel(self) -> None:
        await self._controller.cancel_enter()


class ExitLightingModeSkill(Skill):
    """Fallback direct execution path.

    Normal server invocations orchestrate this as two executor-visible steps:
    leave mode, then invoke the registered ``return_safe`` skill exactly once.
    This fallback keeps composed/direct skill execution safe as well.
    """

    skill_id = "exit_lighting_mode"
    label = "退出照明模式"
    description = (
        "当用户明确要求退出或关闭照明模式时调用。服务端会关闭白色常亮，"
        "并单独执行且只执行一次 return_safe。"
    )
    parameters = {}

    def __init__(self, controller: LightingModeController) -> None:
        self._controller = controller
        self._return_safe = ReturnSafeSkill()

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        del params
        started = await self._controller.begin_exit(reason="direct_skill")
        if not started:
            return SkillResult(
                status="ok",
                data={**self._controller.snapshot(), "already_inactive": True, "return_safe_invoked": False},
            )
        result = await self._return_safe.execute(ctx, velocity=60.0)
        await self._controller.finish_exit(
            return_safe_status=result.status,
            error=result.message if result.status != "ok" else "",
        )
        return SkillResult(
            status=result.status,
            message=result.message,
            data={
                **self._controller.snapshot(),
                "return_safe_invoked": True,
                "return_safe": result.data,
            },
        )

    async def cancel(self) -> None:
        await self._return_safe.cancel()
