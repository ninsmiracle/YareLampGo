"""Expression skills — LED patterns + optional motion combos."""

from __future__ import annotations

from typing import Any

from lampgo.core.led import LED_EXPRESSIONS
from lampgo.core.types import SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext


class SetExpressionSkill(Skill):
    skill_id = "set_expression"
    description = "Set an LED expression (e.g. smiley, heart, angry)."
    parameters = {
        "expression": ParameterSpec(
            name="expression",
            type="str",
            description=f"Expression name. Options: {', '.join(LED_EXPRESSIONS.keys())}",
        ),
        "brightness": ParameterSpec(
            name="brightness", type="int", required=False, default=200, description="Brightness 1-255"
        ),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        expression = params.get("expression") or params.get("mode") or ""
        if not expression:
            return SkillResult(status="error", message="Expression name required")

        if expression.lower().strip() not in LED_EXPRESSIONS:
            valid = ", ".join(LED_EXPRESSIONS.keys())
            return SkillResult(status="error", message=f"Unknown expression: {expression}. Valid: {valid}")

        brightness = params.get("brightness")
        if brightness is not None:
            ctx.led.set_brightness(int(brightness))

        ctx.led.set_mode(expression)
        return SkillResult(status="ok", data={"expression": expression})
