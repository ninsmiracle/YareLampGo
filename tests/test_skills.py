"""Tests for skill registry and executor."""


import pytest

from lampgo.core.config import MotionConfig, SafetyConfig
from lampgo.core.events import EventBus
from lampgo.core.led import LEDConfig, LEDController
from lampgo.core.motion import MotionRuntime
from lampgo.core.safety import SafetyKernel
from lampgo.core.types import SkillResult
from lampgo.skills.base import Skill, SkillContext
from lampgo.skills.builtin.parametric_skills import LookAtSkill
from lampgo.skills.executor import SkillExecutor
from lampgo.skills.registry import SkillRegistry
from tests.conftest import MockHAL


class DummySkill(Skill):
    skill_id = "dummy"
    description = "Test skill"

    async def execute(self, ctx: SkillContext, **params) -> SkillResult:
        return SkillResult(status="ok", data={"echo": params})


def test_registry():
    reg = SkillRegistry()
    reg.register(DummySkill())
    assert "dummy" in reg
    assert len(reg) == 1
    assert reg.get("dummy") is not None
    assert reg.get("nonexistent") is None


@pytest.mark.asyncio
async def test_executor_invoke():
    hal = MockHAL()
    hal.connect()
    safety = SafetyKernel(SafetyConfig())
    motion = MotionRuntime(hal, safety, MotionConfig(tick_rate_hz=100))
    motion.start()

    try:
        events = EventBus()
        led = LEDController(LEDConfig())
        reg = SkillRegistry()
        reg.register(DummySkill())
        executor = SkillExecutor(reg, events)
        ctx = SkillContext(motion=motion, led=led, events=events, state=hal.read_positions())

        result = await executor.invoke("dummy", ctx, foo="bar")
        assert result.status == "ok"
        assert result.result == {"echo": {"foo": "bar"}}
    finally:
        motion.stop()


@pytest.mark.asyncio
async def test_executor_unknown_skill():
    hal = MockHAL()
    hal.connect()
    safety = SafetyKernel(SafetyConfig())
    motion = MotionRuntime(hal, safety, MotionConfig(tick_rate_hz=100))

    events = EventBus()
    led = LEDController(LEDConfig())
    reg = SkillRegistry()
    executor = SkillExecutor(reg, events)
    ctx = SkillContext(motion=motion, led=led, events=events, state=hal.read_positions())

    result = await executor.invoke("nonexistent", ctx)
    assert result.status == "rejected"
    assert result.error_code == "unknown_skill"


@pytest.mark.asyncio
async def test_look_at_keeps_unspecified_axis():
    hal = MockHAL()
    hal.connect()
    hal.write_positions({"base_yaw": 0.0, "base_pitch": -45.0})
    safety = SafetyKernel(SafetyConfig())
    motion = MotionRuntime(hal, safety, MotionConfig(tick_rate_hz=100))
    motion.start()

    try:
        events = EventBus()
        led = LEDController(LEDConfig())
        ctx = SkillContext(motion=motion, led=led, events=events, state=hal.read_positions())

        result = await LookAtSkill().execute(ctx, pitch=-38, velocity=20)

        assert result.status == "ok"
        positions = hal.read_positions().positions
        assert abs(positions["base_yaw"] - 0.0) < 1.0
        assert abs(positions["base_pitch"] - (-38.0)) < 1.0
    finally:
        motion.stop()
