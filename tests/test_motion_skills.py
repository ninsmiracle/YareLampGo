"""Built-in motion skill tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from lampgo.core.events import EventBus
from lampgo.core.types import SkillResult
from lampgo.skills.base import Skill, SkillContext
from lampgo.skills.executor import SkillExecutor
from lampgo.skills.registry import SkillRegistry


def test_return_safe_disables_anticipation():
    from lampgo.core.types import MotionStatus
    from lampgo.skills.builtin.motion_skills import ReturnSafeSkill, set_calibration_home

    class FakeMotion:
        def __init__(self) -> None:
            self.target = None
            self.current_state = SimpleNamespace(get=lambda joint, default=0.0: default)
            self.status = MotionStatus()

        def move_to(self, target):
            import threading

            self.target = target
            done = threading.Event()
            done.set()
            return done

    async def run() -> None:
        motion = FakeMotion()
        set_calibration_home({"base_pitch": -25.4, "elbow_pitch": -25.3})
        result = await ReturnSafeSkill().execute(SimpleNamespace(motion=motion), velocity=60.0)

        assert result.status == "ok"
        assert motion.target.anticipation is False
        assert motion.target.joints["elbow_pitch"] == -25.3

    asyncio.run(run())


def test_executor_preempts_running_skill():
    class SlowSkill(Skill):
        skill_id = "slow_test"

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = False
            self._done = asyncio.Event()

        async def execute(self, ctx, **params):
            del ctx, params
            self.started.set()
            await self._done.wait()
            return SkillResult(status="ok")

        async def cancel(self) -> None:
            self.cancelled = True
            self._done.set()

    class FastSkill(Skill):
        skill_id = "fast_test"

        async def execute(self, ctx, **params):
            del ctx, params
            return SkillResult(status="ok", data={"ran": True})

    async def run() -> None:
        slow = SlowSkill()
        registry = SkillRegistry()
        registry.register(slow)
        registry.register(FastSkill())
        executor = SkillExecutor(registry, EventBus())
        ctx = SimpleNamespace(
            motion=SimpleNamespace(is_running=True),
            led=SimpleNamespace(is_connected=True),
        )

        first = asyncio.create_task(executor.invoke("slow_test", ctx))
        await slow.started.wait()

        second = await executor.invoke("fast_test", ctx)
        first_result = await first

        assert slow.cancelled is True
        assert first_result.status == "cancelled"
        assert second.status == "ok"
        assert second.result == {"ran": True}
        assert executor.is_busy is False

    asyncio.run(run())


def test_executor_rejects_virtual_motion_after_hardware_startup_failure() -> None:
    class FakeReturnSafe(Skill):
        skill_id = "return_safe"

        async def execute(self, ctx, **params):
            del ctx, params
            raise AssertionError("blocked motion must not execute")

    async def run() -> None:
        registry = SkillRegistry()
        registry.register(FakeReturnSafe())
        executor = SkillExecutor(registry, EventBus())
        executor.set_motion_block_reason("Unsafe startup pose; torque remains disabled")
        ctx = SimpleNamespace(
            motion=SimpleNamespace(is_running=True, is_virtual=True),
            led=SimpleNamespace(is_connected=False),
        )

        result = await executor.invoke("return_safe", ctx)

        assert result.status == "rejected"
        assert result.error_code == "motor_hardware_unavailable"
        assert result.error_detail == "Unsafe startup pose; torque remains disabled"

    asyncio.run(run())


class _HangingSkill(Skill):
    skill_id = "first"
    description = "hang until cancelled"
    parameters = {}

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.started = asyncio.Event()
        self.cancel_count = 0

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self.events.append("first:start")
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.events.append("first:finally")

    async def cancel(self) -> None:
        self.cancel_count += 1
        self.events.append("first:cancel")


class _InstantSkill(Skill):
    skill_id = "second"
    description = "finish immediately"
    parameters = {}

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        self.events.append("second:start")
        return SkillResult(status="ok", data={"played": "second"})


@pytest.mark.asyncio
async def test_new_invoke_cancels_running_skill_before_starting_next() -> None:
    events: list[str] = []
    registry = SkillRegistry()
    first = _HangingSkill(events)
    second = _InstantSkill(events)
    registry.register(first)
    registry.register(second)
    executor = SkillExecutor(registry, EventBus())

    first_invoke = asyncio.create_task(executor.invoke("first", ctx=None))  # type: ignore[arg-type]
    await first.started.wait()

    second_result = await executor.invoke("second", ctx=None)  # type: ignore[arg-type]
    first_result = await first_invoke

    assert first_result.status == "cancelled"
    assert second_result.status == "ok"
    assert first.cancel_count == 1
    assert events == ["first:start", "first:cancel", "first:finally", "second:start"]
