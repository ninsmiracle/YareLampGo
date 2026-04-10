"""Tests for skill registry, executor, and parametric skills."""


import pytest

from lampgo.core.config import MotionConfig, SafetyConfig
from lampgo.core.events import EventBus
from lampgo.core.led import LEDConfig, LEDController
from lampgo.core.motion import MotionRuntime
from lampgo.core.safety import SafetyKernel
from lampgo.core.types import SkillResult
from lampgo.skills.base import Skill, SkillContext
from lampgo.skills.builtin.parametric_skills import (
    DanceSkill,
    HeadShakeSkill,
    IdleSwaySkill,
    LookAtSkill,
    NodSkill,
)
from lampgo.skills.executor import SkillExecutor
from lampgo.skills.registry import SkillRegistry
from tests.conftest import MockHAL


def _make_ctx(hal: MockHAL, tick_hz: int = 100) -> tuple[MotionRuntime, SkillContext]:
    safety = SafetyKernel(SafetyConfig())
    motion = MotionRuntime(hal, safety, MotionConfig(tick_rate_hz=tick_hz))
    motion.start()
    ctx = SkillContext(
        motion=motion,
        led=LEDController(LEDConfig()),
        events=EventBus(),
        state=hal.read_positions(),
    )
    return motion, ctx


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
    motion, ctx = _make_ctx(hal)

    try:
        result = await LookAtSkill().execute(ctx, pitch=-38, velocity=20)
        assert result.status == "ok"
        positions = hal.read_positions().positions
        assert abs(positions["base_yaw"] - 0.0) < 1.0
        assert abs(positions["base_pitch"] - (-38.0)) < 1.0
    finally:
        motion.stop()


# ---------------------------------------------------------------------------
# Parametric skills — stream_frames architecture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nod_uses_stream_frames():
    """NodSkill should complete and write many frames (stream, not single move_to)."""
    hal = MockHAL()
    hal.connect()
    motion, ctx = _make_ctx(hal)

    try:
        result = await NodSkill().execute(ctx, amplitude=10.0, speed=120.0, count=2)
    finally:
        motion.stop()

    assert result.status == "ok"
    assert result.data["count"] == 2
    # stream_frames writes many intermediate positions; single move_to would write far fewer
    assert len(hal.write_log) > 10


@pytest.mark.asyncio
async def test_headshake_uses_stream_frames():
    """HeadShakeSkill should complete and produce many frames."""
    hal = MockHAL()
    hal.connect()
    motion, ctx = _make_ctx(hal)

    try:
        result = await HeadShakeSkill().execute(ctx, amplitude=15.0, speed=150.0, count=2)
    finally:
        motion.stop()

    assert result.status == "ok"
    assert result.data["count"] == 2
    assert len(hal.write_log) > 10


@pytest.mark.asyncio
async def test_idle_sway_short_duration():
    """IdleSwaySkill with a very short duration should complete cleanly."""
    hal = MockHAL()
    hal.connect()
    motion, ctx = _make_ctx(hal)

    try:
        result = await IdleSwaySkill().execute(ctx, amplitude=3.0, period=2.0, duration=0.5)
    finally:
        motion.stop()

    assert result.status == "ok"
    assert len(hal.write_log) > 5


@pytest.mark.asyncio
async def test_dance_uses_stream_frames():
    """DanceSkill should complete with many written frames."""
    hal = MockHAL()
    hal.connect()
    motion, ctx = _make_ctx(hal)

    try:
        result = await DanceSkill().execute(ctx, speed=150.0, cycles=1)
    finally:
        motion.stop()

    assert result.status == "ok"
    assert result.data["cycles"] == 1
    assert len(hal.write_log) > 10


@pytest.mark.asyncio
async def test_idle_sway_cancel():
    """Cancelling IdleSwaySkill mid-stream should stop it promptly."""
    import asyncio

    hal = MockHAL()
    hal.connect()
    motion, ctx = _make_ctx(hal)

    skill = IdleSwaySkill()
    try:
        task = asyncio.create_task(skill.execute(ctx, duration=30.0))
        await asyncio.sleep(0.2)
        await skill.cancel()
        result = await asyncio.wait_for(task, timeout=2.0)
    finally:
        motion.stop()

    # After cancel the skill should return quickly (not hang for 30 s)
    assert result.status in ("ok", "cancelled", "error")


@pytest.mark.asyncio
async def test_headshake_cancel():
    """Cancelling HeadShakeSkill mid-stream should stop it promptly."""
    import asyncio

    hal = MockHAL()
    hal.connect()
    motion, ctx = _make_ctx(hal)

    skill = HeadShakeSkill()
    try:
        task = asyncio.create_task(skill.execute(ctx, amplitude=20.0, speed=60.0, count=10))
        await asyncio.sleep(0.2)
        await skill.cancel()
        result = await asyncio.wait_for(task, timeout=2.0)
    finally:
        motion.stop()

    assert result.status in ("ok", "cancelled", "error")


@pytest.mark.asyncio
async def test_skill_context_play_frames():
    """SkillContext.play_frames() should stream frames and return True on success."""
    hal = MockHAL()
    hal.connect()
    motion, ctx = _make_ctx(hal)

    frames = [{"base_yaw": float(i)} for i in range(20)]
    try:
        completed = await ctx.play_frames(frames, fps=50)
    finally:
        motion.stop()

    assert completed is True
    assert len(hal.write_log) >= len(frames)
