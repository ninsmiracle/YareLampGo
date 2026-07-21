"""Skill base class and context — the only way to move the robot."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from lampgo.core.events import EventBus
from lampgo.core.led import LEDController
from lampgo.core.motion import MotionRuntime
from lampgo.core.types import JointState, SkillResult


@dataclass
class ParameterSpec:
    """Describes a single skill parameter for external-agent exposure."""

    name: str
    type: str  # "float", "int", "str", "bool"
    description: str = ""
    required: bool = True
    default: Any = None


async def _await_done(done_event, timeout: float) -> bool:
    """Poll a threading.Event from asyncio until it's set or timeout expires.

    Returns True if the event was set within *timeout* seconds, False otherwise.
    This helper is intentionally not a method on SkillContext so it can be used
    by module-level functions as well.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while not done_event.is_set():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.02)
    return True


@dataclass
class SkillContext:
    """Injected into every skill — provides safe access to subsystems.

    Skills never touch the HAL directly.
    """

    motion: MotionRuntime
    led: LEDController
    events: EventBus
    state: JointState
    clock: Any | None = None

    async def play_frames(
        self,
        frames: list[dict[str, float]],
        fps: int = 50,
        timeout: float | None = None,
    ) -> bool:
        """Stream a pre-computed frame sequence and wait for completion.

        This is the recommended high-level API for all rhythmic/oscillatory
        motions.  It eliminates boilerplate around ``stream_frames`` +
        threading.Event polling.

        Parameters
        ----------
        frames:
            Frame list produced by :func:`~lampgo.core.trajectory.generate_sine_frames`
            or :func:`~lampgo.core.trajectory.generate_waypoint_frames`.
        fps:
            Playback rate in Hz (should match the rate used to generate *frames*).
        timeout:
            Maximum seconds to wait.  Defaults to ``len(frames) / fps + 5.0``.

        Returns
        -------
        bool
            True if playback completed normally, False on timeout.
        """
        if not frames:
            return True
        _timeout = timeout if timeout is not None else len(frames) / max(fps, 1) + 5.0
        done = self.motion.stream_frames(frames, fps=fps)
        return await _await_done(done, timeout=_timeout)


class Skill(ABC):
    """Base class for all lampgo skills.

    Motion API usage guide
    ----------------------
    Choose the correct API based on what the skill does:

    | Motion type              | API                                      | Examples                   |
    |--------------------------|------------------------------------------|----------------------------|
    | Point-to-point (single)  | ``ctx.motion.move_to()``                 | return_safe, look_at       |
    | Pre-computed rhythmic    | ``ctx.play_frames()``                    | nod, headshake, dance,     |
    |                          | (or ``ctx.motion.stream_frames()``       |   idle_sway                |
    |                          |  directly for custom done-event logic)   |                            |
    | Real-time reactive       | ``ctx.motion.update_target(             | face_follow (real)         |
    |                          |     style="linear")``                    |                            |

    **Do NOT** call ``update_target`` in a fixed-rate loop to implement
    scripted motions.  Every call with a non-"linear" style rebuilds the
    trajectory and resets joint velocities, producing the micro-start/stop
    stutter this architecture is designed to prevent.

    Cancellation pattern
    --------------------
    Skills that use ``stream_frames`` / ``play_frames`` must store the
    ``MotionRuntime`` reference and call ``stop_immediate()`` in ``cancel()``::

        class MySinusoidSkill(Skill):
            _motion: MotionRuntime | None = None

            async def execute(self, ctx, **params):
                self._motion = ctx.motion
                try:
                    frames = generate_sine_frames(...)
                    if not await ctx.play_frames(frames, fps=50):
                        return SkillResult(status="error", message="timeout")
                    return SkillResult(status="ok")
                finally:
                    self._motion = None

            async def cancel(self):
                if self._motion:
                    self._motion.stop_immediate()
    """

    skill_id: str = ""
    description: str = ""
    parameters: dict[str, ParameterSpec] = {}
    priority: int = 0  # 0 = normal, higher = higher priority

    # Provenance / display metadata.  Used by the Web UI to split "出厂技能"
    # (hardcoded Python classes, read-only) from "我的技能" (JSON-defined
    # composed skills the user or an agent authored at runtime, editable /
    # deletable).  ``label`` is an optional Chinese display name; when empty
    # the UI falls back to the SKILL_LABELS_CN dictionary keyed by skill_id.
    source: str = "factory"  # "factory" | "user"
    label: str = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.skill_id and cls.__name__ != "Skill":
            cls.skill_id = cls.__name__.lower().removesuffix("skill")

    @abstractmethod
    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        ...

    async def cancel(self) -> None:
        """Called when this skill is being pre-empted. Override to clean up."""

    async def rollback(self) -> None:
        """Called after cancellation if the skill needs to undo side-effects."""
