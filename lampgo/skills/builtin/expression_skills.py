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


class ShowClockSkill(Skill):
    """Show the backend's local time on the S3 LED matrix."""

    skill_id = "show_clock"
    description = "Show the backend local time on the S3 LED display and keep it updated once per minute."
    parameters = {
        "color": ParameterSpec(
            name="color",
            type="str",
            required=False,
            default="#37d6ff",
            description="Clock color as #RRGGBB.",
        ),
        "brightness": ParameterSpec(
            name="brightness",
            type="int",
            required=False,
            default=32,
            description="Clock brightness from 1 to 96.",
        ),
        "effect": ParameterSpec(
            name="effect",
            type="str",
            required=False,
            default="steady",
            description="Clock effect: steady, blink, or orbit.",
        ),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        clock = ctx.clock
        if clock is None:
            return SkillResult(status="error", message="Clock controller unavailable")
        result = clock.show(
            color=params.get("color"),
            brightness=params.get("brightness"),
            effect=params.get("effect"),
        )
        if not result.get("ok"):
            return SkillResult(status="error", message="LED clock could not reach the paired device", data=result)
        return SkillResult(status="ok", data=result)


class StartElectronicOceanSkill(Skill):
    """Start the S3-local fluid renderer driven by wrist pitch feedback."""

    skill_id = "start_electronic_ocean"
    description = (
        "Start electronic ocean on the S3 LED matrix. The current wrist_pitch pose becomes horizontal, "
        "then joint 5 movement disturbs the locally simulated fluid."
    )
    parameters = {
        "dynamics": ParameterSpec(
            "dynamics",
            "str",
            required=False,
            default="standard",
            description="Fluid dynamics preset: soft, standard, or violent.",
        ),
        "color": ParameterSpec(
            "color", "str", required=False, default="#00b8e0", description="Water color as #RRGGBB."
        ),
        "brightness": ParameterSpec("brightness", "int", required=False, default=36, description="Brightness 1 to 96."),
        "fill_percent": ParameterSpec(
            "fill_percent", "int", required=False, default=55, description="Water fill 20 to 80 percent."
        ),
        "sensitivity_percent": ParameterSpec(
            "sensitivity_percent",
            "int",
            required=False,
            default=100,
            description="Motion sensitivity 25 to 200 percent.",
        ),
        "edge_highlight_percent": ParameterSpec(
            "edge_highlight_percent",
            "int",
            required=False,
            default=75,
            description="Surface highlight 0 to 100 percent.",
        ),
        "tilt_percent": ParameterSpec(
            "tilt_percent",
            "int",
            required=False,
            default=100,
            description="Whole-fluid tilt amplitude 50 to 160 percent.",
        ),
        "impact_percent": ParameterSpec(
            "impact_percent",
            "int",
            required=False,
            default=100,
            description="Wall collision and splash strength 0 to 200 percent.",
        ),
    }

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        ocean = ctx.electronic_ocean
        if ocean is None:
            return SkillResult(status="error", message="Electronic ocean controller unavailable")
        result = await ocean.start(**params)
        if not result.get("ok"):
            return SkillResult(
                status="error",
                message="Electronic ocean could not reach the paired device",
                data=result,
            )
        return SkillResult(status="ok", data=result)


class StopElectronicOceanSkill(Skill):
    skill_id = "stop_electronic_ocean"
    description = "Stop electronic ocean and clear the S3 LED matrix."
    parameters = {}

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        ocean = ctx.electronic_ocean
        if ocean is None:
            return SkillResult(status="error", message="Electronic ocean controller unavailable")
        result = await ocean.stop()
        if not result.get("ok"):
            return SkillResult(status="error", message="Electronic ocean stop failed", data=result)
        return SkillResult(status="ok", data=result)
