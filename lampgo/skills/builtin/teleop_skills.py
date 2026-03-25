"""Teleop skills — user physically manipulates the arm to control desktop input.

In teleop mode, the arm's joint positions are mapped to desktop actions:
  - base_yaw delta -> mouse X movement
  - base_pitch delta -> mouse Y movement
  - wrist_roll past threshold -> click
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from lampgo.bridge.desktop import DesktopAction, DesktopBridge
from lampgo.core.types import SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext

logger = structlog.get_logger(__name__)


class TeleopMouseSkill(Skill):
    skill_id = "teleop_mouse"
    description = "Use arm as a mouse controller — move arm to move cursor."
    parameters = {
        "sensitivity": ParameterSpec(
            name="sensitivity", type="float", required=False, default=5.0, description="Mouse sensitivity multiplier"
        ),
        "duration": ParameterSpec(
            name="duration", type="float", required=False, default=60.0, description="Duration in seconds"
        ),
    }

    _cancelled = False

    def __init__(self, bridge: DesktopBridge) -> None:
        self._bridge = bridge

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._cancelled = False
        sensitivity = float(params.get("sensitivity", 5.0))
        duration = float(params.get("duration", 60.0))

        ctx.led.set_mode("star")

        prev_yaw = ctx.state.get("base_yaw", 0.0)
        prev_pitch = ctx.state.get("base_pitch", 0.0)
        elapsed = 0.0
        step = 0.05  # 20Hz polling

        while elapsed < duration and not self._cancelled:
            state = ctx.motion.current_state
            yaw = state.get("base_yaw", prev_yaw)
            pitch = state.get("base_pitch", prev_pitch)

            dx = int((yaw - prev_yaw) * sensitivity)
            dy = int(-(pitch - prev_pitch) * sensitivity)

            if abs(dx) > 0 or abs(dy) > 0:
                self._bridge.execute_action(DesktopAction(action_type="mouse_move", params={"dx": dx, "dy": dy}))

            # Wrist roll click detection
            roll = state.get("wrist_roll", 0.0)
            if abs(roll) > 50:
                self._bridge.execute_action(DesktopAction(action_type="mouse_click", params={"button": "left"}))
                await asyncio.sleep(0.3)

            prev_yaw = yaw
            prev_pitch = pitch
            await asyncio.sleep(step)
            elapsed += step

        ctx.led.set_mode("off")
        return SkillResult(status="ok" if not self._cancelled else "cancelled")

    async def cancel(self) -> None:
        self._cancelled = True


class TeleopGamepadSkill(Skill):
    skill_id = "teleop_gamepad"
    description = "Use arm as a gamepad — map joints to keyboard inputs for gaming."
    parameters = {
        "duration": ParameterSpec(
            name="duration", type="float", required=False, default=120.0, description="Duration in seconds"
        ),
    }

    _cancelled = False

    def __init__(self, bridge: DesktopBridge) -> None:
        self._bridge = bridge

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self._cancelled = False
        duration = float(params.get("duration", 120.0))

        ctx.led.set_mode("rainbow")
        elapsed = 0.0
        step = 0.1

        prev_yaw = ctx.state.get("base_yaw", 0.0)
        yaw_threshold = 15.0

        while elapsed < duration and not self._cancelled:
            state = ctx.motion.current_state
            yaw = state.get("base_yaw", prev_yaw)
            pitch = state.get("base_pitch", 0.0)

            if yaw - prev_yaw > yaw_threshold:
                self._bridge.execute_action(DesktopAction(action_type="key_press", params={"key": "right"}))
            elif prev_yaw - yaw > yaw_threshold:
                self._bridge.execute_action(DesktopAction(action_type="key_press", params={"key": "left"}))

            if pitch < -20:
                self._bridge.execute_action(DesktopAction(action_type="key_press", params={"key": "up"}))
            elif pitch > 20:
                self._bridge.execute_action(DesktopAction(action_type="key_press", params={"key": "down"}))

            prev_yaw = yaw
            await asyncio.sleep(step)
            elapsed += step

        ctx.led.set_mode("off")
        return SkillResult(status="ok" if not self._cancelled else "cancelled")

    async def cancel(self) -> None:
        self._cancelled = True
