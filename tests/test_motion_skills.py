"""Built-in motion skill tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from lampgo.core.events import EventBus
from lampgo.core.types import SkillResult
from lampgo.skills.base import Skill
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
