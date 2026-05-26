"""Tests for :class:`ComposedSkill` — sequencing, cancel propagation,
parameter substitution, and error bubbling.

We avoid instantiating a real :class:`SkillContext` / motion runtime here.
The child skills we compose are fakes that just record the calls they
received, which is exactly what we want to assert on — nothing about
motor behaviour is in scope for this unit.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from lampgo.core.types import JointState, SkillResult
from lampgo.skills.base import Skill
from lampgo.skills.composed import ComposedSkill
from lampgo.skills.loader import validate_definition
from lampgo.skills.registry import SkillRegistry

# ---------- test doubles ---------------------------------------------------


class _RecordingSkill(Skill):
    """Factory-skill test double that records every invocation."""

    source = "factory"

    def __init__(self, skill_id: str, result: SkillResult | None = None) -> None:
        self.skill_id = skill_id
        self.description = f"recording {skill_id}"
        self.parameters = {}
        self._result = result or SkillResult(status="ok")
        self.calls: list[dict[str, Any]] = []
        self.cancel_count = 0

    async def execute(self, ctx, **params):  # type: ignore[override]
        self.calls.append(dict(params))
        return self._result

    async def cancel(self) -> None:  # type: ignore[override]
        self.cancel_count += 1


class _HangingSkill(Skill):
    """Factory-skill double that blocks until cancelled — used to verify
    that the outer executor's cancel signal reaches the correct child."""

    source = "factory"

    def __init__(self, skill_id: str) -> None:
        self.skill_id = skill_id
        self.description = f"hanging {skill_id}"
        self.parameters = {}
        self.cancel_count = 0
        self.started = asyncio.Event()

    async def execute(self, ctx, **params):  # type: ignore[override]
        self.started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # Re-raise so the surrounding ``asyncio.wait_for`` /
            # ComposedSkill.execute sees it as cancellation, not a
            # successful early return.
            raise
        return SkillResult(status="ok")

    async def cancel(self) -> None:  # type: ignore[override]
        self.cancel_count += 1


# ---------- motion/ctx fakes for trajectory steps -------------------------


class _FakeMotion:
    """Captures frames handed to ``stream_frames`` so trajectory-path tests
    can assert *what* was sent to the motor bus without needing a real HAL.

    The returned ``done_event`` is pre-set by default so ``ctx.play_frames``
    completes immediately — individual tests can override for cancel-race
    assertions.
    """

    def __init__(self) -> None:
        self.streamed: list[list[dict]] = []
        self.fps_seen: list[int] = []
        self.stop_immediate_count = 0
        import threading

        self._done = threading.Event()
        self._done.set()

    def stream_frames(self, frames, fps=50):
        self.streamed.append(list(frames))
        self.fps_seen.append(fps)
        return self._done

    def stop_immediate(self):
        self.stop_immediate_count += 1
        self._done.set()


def _ctx_with_motion(motion: _FakeMotion | None = None):
    """Build a minimal SkillContext.  We use a real SkillContext instance
    so the ``ctx.play_frames`` helper is exercised end-to-end — if its
    contract changes this test catches the drift."""
    from lampgo.skills.base import SkillContext

    motion = motion or _FakeMotion()
    return SkillContext(
        motion=motion,  # type: ignore[arg-type]
        led=None,  # type: ignore[arg-type]
        events=None,  # type: ignore[arg-type]
        state=JointState(positions={j: 0.0 for j in (
            "base_yaw", "base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch"
        )}),
    )


# ---------- helpers --------------------------------------------------------


def _build(registry: SkillRegistry, definition: dict[str, Any]) -> ComposedSkill:
    validated = validate_definition(definition, registry=registry)
    skill = ComposedSkill(validated, registry)
    registry.register(skill)
    return skill


# ---------- execution ------------------------------------------------------


@pytest.mark.asyncio
async def test_steps_run_in_order_with_substituted_params():
    reg = SkillRegistry()
    nod = _RecordingSkill("nod")
    expr = _RecordingSkill("set_expression")
    reg.register(nod)
    reg.register(expr)

    skill = _build(
        reg,
        {
            "skill_id": "greet",
            "description": "greet routine",
            "parameters": {
                "mood": {"type": "str", "default": "smiley"},
            },
            "steps": [
                {"skill_id": "set_expression", "params": {"expression": "{mood}"}},
                {"skill_id": "nod", "params": {"count": 2}},
            ],
        },
    )

    result = await skill.execute(ctx=None)  # type: ignore[arg-type]
    assert result.status == "ok"
    assert result.data == {"steps_executed": 2, "total_steps": 2}
    # Order matters — set_expression before nod.
    assert expr.calls == [{"expression": "smiley"}]
    assert nod.calls == [{"count": 2}]


@pytest.mark.asyncio
async def test_default_param_applied_when_caller_omits_it():
    reg = SkillRegistry()
    expr = _RecordingSkill("set_expression")
    reg.register(expr)

    skill = _build(
        reg,
        {
            "skill_id": "greet2",
            "description": "x",
            "parameters": {"mood": {"type": "str", "default": "heart"}},
            "steps": [
                {"skill_id": "set_expression", "params": {"expression": "{mood}"}},
            ],
        },
    )

    await skill.execute(ctx=None)  # type: ignore[arg-type]
    assert expr.calls == [{"expression": "heart"}]


@pytest.mark.asyncio
async def test_missing_placeholder_leaves_template_intact():
    """Unknown ``{foo}`` must not raise — we'd rather let the child skill
    surface a typed error than crash the whole composed run."""
    reg = SkillRegistry()
    expr = _RecordingSkill("set_expression")
    reg.register(expr)

    skill = _build(
        reg,
        {
            "skill_id": "greet3",
            "description": "x",
            "steps": [
                {"skill_id": "set_expression", "params": {"expression": "{unset}"}},
            ],
        },
    )
    await skill.execute(ctx=None)  # type: ignore[arg-type]
    assert expr.calls == [{"expression": "{unset}"}]


@pytest.mark.asyncio
async def test_step_error_bubbles_up_with_step_index():
    reg = SkillRegistry()
    ok1 = _RecordingSkill("nod")
    boom = _RecordingSkill("headshake", result=SkillResult(status="error", message="stuck joint"))
    ok2 = _RecordingSkill("dance")
    for s in (ok1, boom, ok2):
        reg.register(s)

    skill = _build(
        reg,
        {
            "skill_id": "chain",
            "description": "x",
            "steps": [
                {"skill_id": "nod"},
                {"skill_id": "headshake"},
                {"skill_id": "dance"},
            ],
        },
    )

    result = await skill.execute(ctx=None)  # type: ignore[arg-type]
    assert result.status == "error"
    # The error message must identify WHICH step failed — without the index
    # debugging a long composed skill from logs is miserable.
    assert "step 1" in result.message
    assert "headshake" in result.message
    # Subsequent steps must not run after an error.
    assert ok2.calls == []


@pytest.mark.asyncio
async def test_cancel_forwards_to_currently_running_child():
    reg = SkillRegistry()
    first = _RecordingSkill("nod")
    hanger = _HangingSkill("set_expression")
    never = _RecordingSkill("dance")
    for s in (first, hanger, never):
        reg.register(s)

    skill = _build(
        reg,
        {
            "skill_id": "cancellable",
            "description": "x",
            "steps": [
                {"skill_id": "nod"},
                {"skill_id": "set_expression"},
                {"skill_id": "dance"},
            ],
        },
    )

    task = asyncio.create_task(skill.execute(ctx=None))  # type: ignore[arg-type]
    await hanger.started.wait()
    # Outer executor would fire this pair in quick succession on preemption.
    await skill.cancel()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert first.calls == [{}]
    # The hanging child is exactly the one that must receive cancel()
    # so it can stop the motor — this is the whole point of the test.
    assert hanger.cancel_count >= 1
    assert never.calls == []


# ---------- Level 2: trajectory step execution ----------------------------


def _trajectory_definition(skill_id: str) -> dict:
    return {
        "skill_id": skill_id,
        "description": "simple yaw wiggle",
        "steps": [
            {
                "trajectory": {
                    "waypoints": [
                        {"joints": {"base_yaw": 0.0}, "duration": 0.0},
                        {"joints": {"base_yaw": 10.0}, "duration": 0.1},
                        {"joints": {"base_yaw": 0.0}, "duration": 0.1},
                    ],
                    "fps": 50,
                    "interpolation": "ease_in_out_cubic",
                }
            }
        ],
    }


@pytest.mark.asyncio
async def test_trajectory_step_generates_and_streams_frames():
    reg = SkillRegistry()
    skill = _build(reg, _trajectory_definition("wiggle"))
    motion = _FakeMotion()
    ctx = _ctx_with_motion(motion)

    result = await skill.execute(ctx=ctx)
    assert result.status == "ok"
    # Exactly one stream_frames call (one trajectory step) with > 0 frames.
    assert len(motion.streamed) == 1
    assert motion.fps_seen == [50]
    frames = motion.streamed[0]
    assert len(frames) > 0
    # The underlying ``generate_waypoint_frames`` emits the first frame at
    # ``t = 1/n`` (the starting pose is assumed to already be where the
    # robot is, not re-sent).  So we assert *shape* not literal 0.0:
    # the trajectory rises toward +10°, then returns near 0°.
    yaws = [f["base_yaw"] for f in frames]
    assert max(yaws) == pytest.approx(10.0, abs=0.1)
    # Final frame must land on the return waypoint.
    assert yaws[-1] == pytest.approx(0.0, abs=0.1)
    # Motion must be monotonic "up-then-down" — we never want the servo
    # bus to chatter with direction reversals inside a single segment.
    peak_idx = yaws.index(max(yaws))
    up_leg = yaws[: peak_idx + 1]
    down_leg = yaws[peak_idx:]
    assert up_leg == sorted(up_leg)
    assert down_leg == sorted(down_leg, reverse=True)


@pytest.mark.asyncio
async def test_trajectory_cancel_calls_stop_immediate():
    """When a trajectory step is cancelled mid-play we must invoke
    ``motion.stop_immediate`` so the servo bus stops *now*, not at the
    end of the buffered frame stream."""
    import threading

    reg = SkillRegistry()
    skill = _build(reg, _trajectory_definition("long_wiggle"))
    motion = _FakeMotion()
    # Replace the pre-set done event with an unset one so play_frames
    # actually blocks, letting us race cancel() against it.
    motion._done = threading.Event()
    ctx = _ctx_with_motion(motion)

    task = asyncio.create_task(skill.execute(ctx=ctx))
    # Give the coroutine a tick to enter play_frames.
    await asyncio.sleep(0.05)
    await skill.cancel()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert motion.stop_immediate_count >= 1


@pytest.mark.asyncio
async def test_trajectory_and_skill_steps_interleave():
    """A hybrid skill runs each step type through its correct path."""
    reg = SkillRegistry()
    nod = _RecordingSkill("nod")
    reg.register(nod)

    definition = validate_definition(
        {
            "skill_id": "hybrid_run",
            "description": "x",
            "steps": [
                {"skill_id": "nod", "params": {"count": 1}},
                {
                    "trajectory": {
                        "waypoints": [
                            {"joints": {"base_yaw": 0.0}, "duration": 0.0},
                            {"joints": {"base_yaw": 15.0}, "duration": 0.15},
                        ],
                        "fps": 50,
                        "interpolation": "linear",
                    }
                },
            ],
        },
        registry=reg,
    )
    skill = ComposedSkill(definition, reg)
    reg.register(skill)
    motion = _FakeMotion()
    ctx = _ctx_with_motion(motion)

    result = await skill.execute(ctx=ctx)
    assert result.status == "ok"
    assert nod.calls == [{"count": 1}]
    assert len(motion.streamed) == 1
    assert motion.streamed[0][-1]["base_yaw"] == pytest.approx(15.0, abs=0.01)
