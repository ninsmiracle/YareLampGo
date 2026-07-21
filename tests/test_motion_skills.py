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


def test_return_safe_recovery_prevalidates_guarded_path_before_streaming():
    from lampgo.skills.builtin.motion_skills import (
        RECOVERY_HOME_FPS,
        RECOVERY_HOME_VELOCITY,
        ReturnSafeSkill,
        get_safe_position,
        set_calibration_home,
    )

    class FakeRecoveryMotion:
        def __init__(self) -> None:
            self.recovery_required = True
            self.current_state = SimpleNamespace(positions=get_safe_position())
            self.prepared: tuple[dict[str, float], float, int] | None = None
            self.streamed = False
            self.completed = False

        def prepare_recovery(self, target, *, max_velocity, fps):
            self.prepared = (dict(target), max_velocity, fps)
            return [dict(target)]

        def stream_recovery_frames(self, frames, fps):
            import threading

            assert self.prepared is not None
            assert frames == [self.prepared[0]]
            assert fps == RECOVERY_HOME_FPS
            self.streamed = True
            done = threading.Event()
            done.set()
            return done

        def complete_recovery(self):
            self.completed = True
            self.recovery_required = False

        def abort_recovery(self):
            raise AssertionError("successful recovery must not abort")

    async def run() -> None:
        set_calibration_home(
            {
                "base_yaw": 0.0,
                "base_pitch": 27.3,
                "elbow_pitch": -0.9,
                "wrist_roll": 22.5,
                "wrist_pitch": -4.9,
            }
        )
        motion = FakeRecoveryMotion()
        motion.current_state = SimpleNamespace(positions=get_safe_position())

        result = await ReturnSafeSkill().execute(SimpleNamespace(motion=motion), velocity=60.0)

        assert result.status == "ok"
        assert motion.prepared is not None
        assert motion.prepared[1:] == (RECOVERY_HOME_VELOCITY, RECOVERY_HOME_FPS)
        assert motion.streamed is True
        assert motion.completed is True

    asyncio.run(run())


def test_return_safe_recovery_velocity_is_thirty_degrees_per_second():
    from lampgo.skills.builtin.motion_skills import RECOVERY_HOME_VELOCITY

    assert RECOVERY_HOME_VELOCITY == 30.0


def test_return_safe_logged_three_degree_elbow_error_unblocks_next_motion():
    from lampgo.skills.builtin.motion_skills import ReturnSafeSkill, get_safe_position, set_calibration_home

    class NearSafeRecoveryMotion:
        def __init__(self) -> None:
            self.recovery_required = True
            self.is_running = True
            positions = get_safe_position()
            positions["elbow_pitch"] += 3.05
            self.current_state = SimpleNamespace(positions=positions)
            self.completed = False

        def prepare_recovery(self, target, *, max_velocity, fps):
            del max_velocity, fps
            return [dict(target)]

        def stream_recovery_frames(self, frames, fps):
            import threading

            del frames, fps
            done = threading.Event()
            done.set()
            return done

        def complete_recovery(self):
            self.completed = True
            self.recovery_required = False

        def abort_recovery(self):
            raise AssertionError("a safe three-degree residual must not release torque")

    class FakeIdleSway(Skill):
        skill_id = "idle_sway"

        async def execute(self, ctx, **params):
            del ctx, params
            return SkillResult(status="ok", data={"started": True})

    async def run() -> None:
        set_calibration_home(
            {
                "base_yaw": 1.3,
                "base_pitch": 27.3,
                "elbow_pitch": -0.9,
                "wrist_roll": 22.5,
                "wrist_pitch": -4.9,
            }
        )
        motion = NearSafeRecoveryMotion()
        registry = SkillRegistry()
        registry.register(ReturnSafeSkill())
        registry.register(FakeIdleSway())
        executor = SkillExecutor(registry, EventBus())
        executor.set_motion_block_reason("Recovery required", allow_return_safe_recovery=True)
        ctx = SimpleNamespace(motion=motion, led=SimpleNamespace(is_connected=False), clock=None)

        recovered = await executor.invoke("return_safe", ctx)
        next_motion = await executor.invoke("idle_sway", ctx)

        assert recovered.status == "ok"
        assert motion.completed is True
        assert next_motion.status == "ok"
        assert next_motion.result == {"started": True}

    asyncio.run(run())


def test_return_safe_recovery_error_keeps_torque_enabled():
    from lampgo.skills.builtin.motion_skills import ReturnSafeSkill, get_safe_position

    class FailedRecoveryMotion:
        def __init__(self) -> None:
            self.recovery_required = True
            self.recovery_error = "Recovery feedback watchdog stopped motion: base_pitch stalled"
            self.current_state = SimpleNamespace(positions=get_safe_position())
            self.aborted = False

        def prepare_recovery(self, target, *, max_velocity, fps):
            del max_velocity, fps
            return [dict(target)]

        def stream_recovery_frames(self, frames, fps):
            import threading

            del frames, fps
            done = threading.Event()
            done.set()
            return done

        def abort_recovery(self):
            self.aborted = True

        def stop_immediate(self):
            pass

    async def run() -> None:
        motion = FailedRecoveryMotion()
        result = await ReturnSafeSkill().execute(SimpleNamespace(motion=motion), velocity=60.0)

        assert result.status == "error"
        assert "feedback watchdog" in result.message
        assert motion.aborted is False

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


def test_executor_allows_only_return_safe_while_recovery_is_required() -> None:
    class FakeReturnSafe(Skill):
        skill_id = "return_safe"

        async def execute(self, ctx, **params):
            del params
            ctx.motion.recovery_required = False
            return SkillResult(status="ok", data={"recovered": True})

    class FakeMoveTo(Skill):
        skill_id = "move_to"

        async def execute(self, ctx, **params):
            del ctx, params
            raise AssertionError("ordinary motion must stay blocked during recovery")

    async def run() -> None:
        registry = SkillRegistry()
        registry.register(FakeReturnSafe())
        registry.register(FakeMoveTo())
        executor = SkillExecutor(registry, EventBus())
        executor.set_motion_block_reason(
            "Recovery required",
            allow_return_safe_recovery=True,
        )
        ctx = SimpleNamespace(
            motion=SimpleNamespace(is_running=False, recovery_required=True),
            led=SimpleNamespace(is_connected=False),
        )

        blocked = await executor.invoke("move_to", ctx)
        recovered = await executor.invoke("return_safe", ctx)

        assert blocked.status == "rejected"
        assert recovered.status == "ok"
        assert recovered.result == {"recovered": True}

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
