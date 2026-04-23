"""ComposedSkill — user-/OpenClaw-authored skill built by sequencing existing
factory skills and/or custom joint-trajectory keyframes.

Motivation
----------
The factory skills (``move_to``, ``nod``, ``set_expression``, ``play_recording``,
…) cover the atomic vocabulary, but many user-visible behaviours are really a
**fixed sequence** of those atoms — optionally interleaved with a one-off
custom joint-trajectory when no existing skill matches the shape of the
motion.  Rather than force users to edit Python + restart, we let OpenClaw /
the Web UI drop a JSON file into ``~/.lampgo/skills/user/`` and pick it up
as a real registered skill.

Step shapes
-----------
A composed skill is an ordered list of steps; each step is exactly one of:

* **Skill-call (Level 1):** ``{"skill_id": "...", "params": {...}}`` — calls
  a registered factory skill directly (not via ``SkillExecutor`` — see note
  below).
* **Trajectory (Level 2):** ``{"trajectory": {"waypoints": [...], "fps": N,
  "interpolation": "..."}}`` — plays a keyframe-interpolated joint
  trajectory via :func:`~lampgo.core.trajectory.generate_waypoint_frames`
  and :meth:`SkillContext.play_frames`.

Design constraints
------------------
1. **Cannot go through :class:`SkillExecutor`.**  The executor is single-slotted;
   invoking a sub-skill via executor would cancel the outer ComposedSkill.
   So we call ``child.execute(ctx, **params)`` directly on the child object.
2. **No composed-calls-composed.**  All ``skill_id`` step targets must refer
   to factory skills (validated at load time in :mod:`lampgo.skills.loader`).
   This eliminates recursion risk and makes the safety model trivial.
3. **Cancellation forwards through whichever sub-step is currently live.**
   For skill-call steps we forward ``cancel()`` to the child skill; for
   trajectory steps we call ``MotionRuntime.stop_immediate()`` so the
   underlying frame stream terminates promptly.
4. **estop is never composable.**  The loader rejects any step that tries to
   embed ``estop`` — that's a safety primitive, not a building block.
5. **Trajectory frames are (re)generated at execute time**, not at load
   time, so live ``SafetyConfig.max_velocity`` changes take effect without
   forcing a re-save of every composed skill on disk.

Parameter substitution
----------------------
Step params may reference outer params with ``{param_name}`` inside strings;
non-string step params are passed through unchanged.  Deliberately simple —
we are not building a DSL, we are building an action macro.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from lampgo.core.motion import MotionRuntime
from lampgo.core.trajectory import generate_waypoint_frames
from lampgo.core.types import SkillResult
from lampgo.skills.base import ParameterSpec, Skill, SkillContext
from lampgo.skills.registry import SkillRegistry

logger = structlog.get_logger(__name__)


MAX_STEPS_PER_SKILL = 20
"""Hard cap on steps-per-composed-skill to keep worst-case runtime bounded."""


class ComposedSkill(Skill):
    """A skill defined by a sequence of factory-skill invocations.

    Parameters
    ----------
    definition:
        Already-validated dict from :mod:`lampgo.skills.loader`.  Must contain
        ``skill_id``, ``description``, ``steps``; may contain ``label`` and
        ``parameters``.
    registry:
        The central :class:`SkillRegistry` used to look up factory children at
        execute time.  We hold a reference (not a snapshot) so that live
        updates to the registry (rare — today the only mutations are at
        startup) are reflected.
    """

    source = "user"

    def __init__(self, definition: dict[str, Any], registry: SkillRegistry) -> None:
        self._definition = definition
        self._registry = registry
        # Only one sub-step is in flight at a time; exactly one of these two
        # attributes is non-None between the ``child.execute`` / ``play_frames``
        # call and its return.  ``cancel()`` consults both.
        self._current_child: Skill | None = None
        self._current_motion: MotionRuntime | None = None

        self.skill_id = str(definition["skill_id"])
        self.label = str(definition.get("label", "") or "")
        self.description = str(definition.get("description", "") or "")
        self.parameters = self._build_parameter_specs(
            definition.get("parameters") or {}
        )
        self._steps: list[dict[str, Any]] = list(definition.get("steps") or [])

    @staticmethod
    def _build_parameter_specs(spec: dict[str, Any]) -> dict[str, ParameterSpec]:
        out: dict[str, ParameterSpec] = {}
        for name, raw in (spec or {}).items():
            if not isinstance(raw, dict):
                continue
            out[name] = ParameterSpec(
                name=name,
                type=str(raw.get("type", "string")),
                description=str(raw.get("description", "")),
                required=bool(raw.get("required", False)),
                default=raw.get("default"),
            )
        return out

    @property
    def definition(self) -> dict[str, Any]:
        """Return a deep copy of the source JSON definition (for persistence)."""
        import copy

        return copy.deepcopy(self._definition)

    async def execute(self, ctx: SkillContext, **params: Any) -> SkillResult:
        # Merge provided params on top of declared defaults so step-level
        # ``{placeholder}`` substitution always sees a value.
        effective = {
            name: spec.default
            for name, spec in self.parameters.items()
            if spec.default is not None
        }
        effective.update({k: v for k, v in params.items() if v is not None})

        executed = 0
        for idx, step in enumerate(self._steps):
            if idx >= MAX_STEPS_PER_SKILL:
                logger.warning(
                    "composed.step_cap",
                    skill_id=self.skill_id,
                    limit=MAX_STEPS_PER_SKILL,
                )
                break

            # Dispatch on step shape.  The loader already rejected malformed
            # steps, so here we only need to pick the correct execution path.
            if "trajectory" in step:
                step_result = await self._execute_trajectory_step(ctx, step, idx)
            else:
                step_result = await self._execute_skill_step(ctx, step, idx, effective)

            executed += 1
            if step_result.status == "error":
                # Bubble up with context — which step failed is much more
                # actionable than just echoing the child's message.
                return step_result
            if step_result.status == "cancelled":
                return step_result

        return SkillResult(
            status="ok",
            data={"steps_executed": executed, "total_steps": len(self._steps)},
        )

    async def _execute_skill_step(
        self,
        ctx: SkillContext,
        step: dict[str, Any],
        idx: int,
        effective_outer_params: dict[str, Any],
    ) -> SkillResult:
        """Run a Level 1 "call factory skill" step."""
        child_id = str(step.get("skill_id", "")).strip()
        child = self._registry.get(child_id) if child_id else None
        if child is None:
            return SkillResult(
                status="error",
                message=f"step {idx}: sub-skill '{child_id}' not registered at execute time",
            )

        step_params = self._resolve_step_params(
            step.get("params") or {}, effective_outer_params
        )

        self._current_child = child
        try:
            child_result = await child.execute(ctx, **step_params)
        except asyncio.CancelledError:
            # Parent executor is aborting us; propagate cancel down so the
            # child releases motor/LED without the coroutine slipping out
            # in a running state.
            try:
                await child.cancel()
            except Exception:
                logger.exception(
                    "composed.child_cancel_error",
                    skill_id=self.skill_id,
                    child_id=child_id,
                )
            raise
        finally:
            self._current_child = None

        if child_result.status == "error":
            return SkillResult(
                status="error",
                message=f"step {idx} ({child_id}): {child_result.message or ''}",
            )
        return child_result

    async def _execute_trajectory_step(
        self,
        ctx: SkillContext,
        step: dict[str, Any],
        idx: int,
    ) -> SkillResult:
        """Run a Level 2 "custom keyframe trajectory" step.

        Frames are generated fresh each invocation (cheap, O(total_fps *
        duration)) so any runtime-adjusted ``SafetyConfig.max_velocity``
        takes effect immediately without having to re-save the JSON.
        """
        traj = step["trajectory"]
        waypoints_raw = traj["waypoints"]

        # SkillContext carries ``state`` (the JointState snapshot) which we
        # use as the implicit pose for the *first* waypoint if the user
        # only specifies the joints they care about.  Otherwise a 2-DoF
        # nod-style trajectory would null all other joints to 0 on frame 0,
        # which is a violent jump.
        base_pose = dict(ctx.state.positions) if ctx and ctx.state else {}

        # Adapt to the (joints, duration) tuple contract expected by
        # generate_waypoint_frames.  The loader already normalised shapes.
        tuples: list[tuple[dict[str, float], float]] = []
        for wp_idx, wp in enumerate(waypoints_raw):
            wp_joints = dict(wp.get("joints") or {})
            # For the very first waypoint, fill un-specified joints from the
            # live pose so the trajectory starts exactly where the robot
            # currently is — no invisible first-frame snap.
            if wp_idx == 0:
                for j, v in base_pose.items():
                    wp_joints.setdefault(j, v)
            tuples.append((wp_joints, float(wp.get("duration", 0.0))))

        # Pull the live safety cap so auto-segment-stretching reflects the
        # operator's current preference, not whatever was active when this
        # JSON was written.
        safety_max_v = 180.0
        try:
            safety_cfg = getattr(getattr(ctx.motion, "_config", None), "safety", None)
            if safety_cfg is not None and getattr(safety_cfg, "max_velocity", None):
                safety_max_v = float(safety_cfg.max_velocity)
        except Exception:
            # Non-fatal: fall back to the generator's default cap.
            pass

        try:
            frames = generate_waypoint_frames(
                waypoints=tuples,
                fps=int(traj.get("fps", 50)),
                ease_fn=str(traj.get("interpolation", "ease_in_out_cubic")),
                ease_overshoot=float(traj.get("ease_overshoot", 0.10)),
                safety_max_velocity=safety_max_v,
            )
        except Exception as exc:
            logger.exception(
                "composed.trajectory_generation_failed",
                skill_id=self.skill_id,
                step_idx=idx,
            )
            return SkillResult(
                status="error",
                message=f"step {idx} trajectory: frame generation failed: {exc}",
            )

        if not frames:
            # An empty frame list happens when all segments are zero-length.
            # Treat as a no-op rather than an error — the user probably
            # intended a "pose hold" that didn't need frames.
            return SkillResult(status="ok")

        self._current_motion = ctx.motion
        try:
            ok = await ctx.play_frames(frames, fps=int(traj.get("fps", 50)))
        except asyncio.CancelledError:
            try:
                ctx.motion.stop_immediate()
            except Exception:
                logger.exception(
                    "composed.trajectory_cancel_stop_failed",
                    skill_id=self.skill_id,
                )
            raise
        finally:
            self._current_motion = None

        if not ok:
            return SkillResult(
                status="error",
                message=f"step {idx} trajectory: play_frames timed out",
            )
        return SkillResult(status="ok")

    async def cancel(self) -> None:
        """Forward cancellation to whichever sub-step is currently live.

        Exactly one of ``_current_child`` or ``_current_motion`` should be
        non-None at any moment — we check both because ``cancel()`` is
        allowed to race against a step transition and we'd rather fire a
        harmless ``stop_immediate`` than miss the active trajectory.
        """
        motion = self._current_motion
        if motion is not None:
            try:
                motion.stop_immediate()
            except Exception:
                logger.exception(
                    "composed.cancel_motion_stop_failed",
                    skill_id=self.skill_id,
                )

        child = self._current_child
        if child is not None:
            try:
                await child.cancel()
            except Exception:
                logger.exception(
                    "composed.cancel_forward_error",
                    skill_id=self.skill_id,
                    child_id=getattr(child, "skill_id", ""),
                )

    @staticmethod
    def _resolve_step_params(
        raw: dict[str, Any], outer: dict[str, Any]
    ) -> dict[str, Any]:
        """Substitute ``{name}`` placeholders in string step params.

        Non-string values are passed through unchanged — numeric joint targets
        do not benefit from template expansion and silently losing type
        information would be a foot-gun.
        """
        out: dict[str, Any] = {}
        for k, v in (raw or {}).items():
            if isinstance(v, str) and "{" in v and "}" in v:
                try:
                    out[k] = v.format_map(_SafeFormatDict(outer))
                except Exception:
                    # Fall back to the literal template on bad substitution;
                    # the child skill will surface a meaningful error.
                    out[k] = v
            else:
                out[k] = v
        return out


class _SafeFormatDict(dict):
    """``format_map`` helper that leaves unknown placeholders as-is instead
    of blowing up with KeyError — lets step authors write ``{optional}`` that
    may or may not be supplied by the outer skill call."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
