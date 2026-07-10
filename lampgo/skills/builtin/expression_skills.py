"""Expression skills — LED patterns + optional motion combos."""

from __future__ import annotations

from typing import Any

from lampgo.core.led import LED_EXPRESSIONS, canonical_expression_name
from lampgo.core.types import SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext


class SetExpressionSkill(Skill):
    skill_id = "set_expression"
    description = "Play a dynamic expression preset, eye clip, or LED effect without saving a new preset."
    parameters = {
        "expression": ParameterSpec(
            name="expression",
            type="str",
            description=(
                "Expression preset, eye clip, or LED effect id. Built-ins include: "
                f"{', '.join(LED_EXPRESSIONS.keys())}. Dynamic ids are listed by /api/expressions."
            ),
        ),
        "brightness": ParameterSpec(
            name="brightness", type="int", required=False, default=200, description="Brightness 1-255"
        ),
        "playback": ParameterSpec(
            name="playback", type="str", required=False, default="once", description="once or loop"
        ),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        expression = params.get("expression") or params.get("mode") or ""
        if not expression:
            return SkillResult(status="error", message="Expression name required")

        canonical = canonical_expression_name(str(expression))
        if canonical is None:
            ok, composition = ctx.led.play_expression(
                str(expression),
                playback=str(params.get("playback") or "once"),
                led_params={"brightness": min(96, int(params.get("brightness") or 64))},
            )
            if not ok or composition is None:
                valid = ", ".join(LED_EXPRESSIONS.keys())
                return SkillResult(
                    status="error",
                    message=f"Unknown or unavailable expression: {expression}. Built-ins: {valid}",
                )
            return SkillResult(status="ok", data={"expression": str(expression), "composition": composition})

        brightness = params.get("brightness")
        if brightness is not None:
            if not ctx.led.set_brightness(int(brightness)):
                return SkillResult(status="error", message="LED controller not connected")

        if not ctx.led.set_mode(canonical):
            return SkillResult(status="error", message="LED controller not connected or expression send failed")
        return SkillResult(
            status="ok",
            data={
                "expression": canonical,
                "requested_expression": str(expression),
                "mode": LED_EXPRESSIONS[canonical],
            },
        )
