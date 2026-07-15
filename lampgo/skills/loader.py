"""User skill loader — persists / loads JSON-defined :class:`ComposedSkill`.

File layout
-----------
``~/.lampgo/skills/user/<skill_id>.json`` — one file per user skill.
Kept deliberately flat (no subdirectories) so that filesystem listing is the
source of truth; deleting the file == deleting the skill.  The directory is
gitignored: user skills are machine-local artefacts, not repo content.

Step shapes
-----------
A step is an object that has **exactly one** of the following discriminator
keys (checked in order below):

* ``skill_id`` — Level 1: call a registered factory skill with ``params``.
* ``trajectory`` — Level 2: play a custom keyframe trajectory.  The payload
  is handed to :func:`lampgo.core.trajectory.generate_waypoint_frames` which
  already enforces per-axis velocity safety via auto-segment-stretching.

Safety rails enforced by :func:`validate_definition`
----------------------------------------------------
1. ``skill_id`` must match ``^[a-z][a-z0-9_]*$`` and be ≤ 64 chars.
2. ``skill_id`` must NOT collide with any factory skill currently in the
   registry (users can't "replace" built-ins; deletion would leave agent tools
   broken).
3. Steps must be non-empty and ≤ :data:`lampgo.skills.composed.MAX_STEPS_PER_SKILL`.
4. Every factory-call step's ``skill_id`` must refer to an allowed *factory*
   skill.  Composed → composed composition is forbidden (eliminates
   recursion risk).
5. ``estop`` is never allowed in a step — composing the emergency stop would
   be a safety foot-gun.
6. Trajectory waypoints: joint names must be in :data:`JOINT_NAMES`, every
   position must be within :data:`DEFAULT_JOINT_LIMITS`, interpolation must
   be in :data:`TRAJECTORY_EASE_WHITELIST`, waypoint count ≤
   :data:`MAX_WAYPOINTS_PER_TRAJECTORY`, total segment duration ≤
   :data:`MAX_TRAJECTORY_DURATION_S`, ``fps`` in [:data:`MIN_FPS`,
   :data:`MAX_FPS`].  Per-segment velocity safety is handled at frame-gen
   time by the trajectory module (it auto-stretches over-fast segments
   rather than rejecting them — gentler UX for humans eyeballing values).

All validator errors are :class:`SkillDefinitionError` and carry a reason code
plus a human-readable message, both returned verbatim to callers so they can give
the user actionable feedback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from lampgo.core.config import DEFAULT_JOINT_LIMITS
from lampgo.core.style import EASE_FUNCTIONS
from lampgo.core.types import JOINT_NAMES
from lampgo.skills.composed import MAX_STEPS_PER_SKILL, ComposedSkill
from lampgo.skills.registry import SkillRegistry

logger = structlog.get_logger(__name__)


USER_SKILLS_DIRNAME = "skills/user"
"""Relative to the lampgo user home (``~/.lampgo``)."""

SKILL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

FORBIDDEN_STEP_SKILLS = {"estop"}
"""Skills that must never appear inside a composed definition."""

# ---- Level 2 (trajectory) caps -------------------------------------------
MAX_WAYPOINTS_PER_TRAJECTORY = 50
MAX_TRAJECTORY_DURATION_S = 30.0
MIN_FPS = 10
MAX_FPS = 100

TRAJECTORY_EASE_WHITELIST = frozenset(EASE_FUNCTIONS.keys()) | {"ease_out_back"}
"""Whitelist for ``trajectory.interpolation``.  Sourced directly from
``EASE_FUNCTIONS`` (plus ``ease_out_back`` which is handled via a separate
overshoot-capable code path in the generator) so there's a single source of
truth; if we add a new ease function to ``lampgo.core.style`` it becomes
available to user skills for free without touching this validator."""


class SkillDefinitionError(ValueError):
    """Raised when a user skill definition fails validation.

    Carries a machine-readable ``reason`` alongside the human-readable
    message so an agent can branch on it (``duplicate_skill`` vs
    ``invalid_step``) without parsing English.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class LoadReport:
    """Summary of a loader run for logging / UI feedback."""

    loaded: list[str]
    errors: list[tuple[str, str]]  # (file_path, error message)


def user_skills_dir(lampgo_home: Path) -> Path:
    return lampgo_home / USER_SKILLS_DIRNAME


def _validate_skill_step(
    step: dict[str, Any], idx: int, *, registry: SkillRegistry
) -> dict[str, Any]:
    """Validate + normalise a Level 1 "call factory skill" step."""
    step_skill_id = str(step.get("skill_id", "") or "").strip()
    if step_skill_id in FORBIDDEN_STEP_SKILLS:
        raise SkillDefinitionError(
            "forbidden_step",
            f"step[{idx}].skill_id '{step_skill_id}' is a safety primitive and cannot be composed",
        )
    target = registry.get(step_skill_id)
    if target is None:
        raise SkillDefinitionError(
            "unknown_step_skill",
            f"step[{idx}].skill_id '{step_skill_id}' is not registered",
        )
    if getattr(target, "source", "factory") != "factory":
        raise SkillDefinitionError(
            "composed_step_forbidden",
            (
                f"step[{idx}].skill_id '{step_skill_id}' is itself a user/composed "
                "skill; composed-calls-composed is not supported (inline the steps "
                "instead)."
            ),
        )
    step_params = step.get("params", {})
    if step_params is None:
        step_params = {}
    if not isinstance(step_params, dict):
        raise SkillDefinitionError(
            "invalid_step_params",
            f"step[{idx}].params must be an object or omitted",
        )
    return {"skill_id": step_skill_id, "params": dict(step_params)}


def _validate_trajectory_step(step: dict[str, Any], idx: int) -> dict[str, Any]:
    """Validate + normalise a Level 2 "custom trajectory" step.

    We deliberately do NOT pre-generate the frames here — two reasons:
    1. It keeps the validator pure (no motion-module import needed at load
       time, and no frame list on disk).
    2. ``generate_waypoint_frames`` needs ``safety_max_velocity`` from the
       runtime's SafetyConfig, which isn't available at load time.  Fresh
       expansion at execute-time is cheap (O(total_fps * duration)) and
       picks up any live safety changes automatically.
    """
    traj = step.get("trajectory")
    if not isinstance(traj, dict):
        raise SkillDefinitionError(
            "invalid_trajectory",
            f"step[{idx}].trajectory must be an object",
        )

    waypoints = traj.get("waypoints")
    if not isinstance(waypoints, list) or len(waypoints) < 2:
        raise SkillDefinitionError(
            "invalid_trajectory",
            f"step[{idx}].trajectory.waypoints must be an array with ≥ 2 entries",
        )
    if len(waypoints) > MAX_WAYPOINTS_PER_TRAJECTORY:
        raise SkillDefinitionError(
            "too_many_waypoints",
            f"step[{idx}].trajectory.waypoints cannot exceed {MAX_WAYPOINTS_PER_TRAJECTORY} (got {len(waypoints)})",
        )

    fps = traj.get("fps", 50)
    try:
        fps = int(fps)
    except (TypeError, ValueError) as exc:
        raise SkillDefinitionError(
            "invalid_trajectory",
            f"step[{idx}].trajectory.fps must be an integer",
        ) from exc
    if not (MIN_FPS <= fps <= MAX_FPS):
        raise SkillDefinitionError(
            "invalid_trajectory",
            f"step[{idx}].trajectory.fps must be in [{MIN_FPS}, {MAX_FPS}] (got {fps})",
        )

    interpolation = str(traj.get("interpolation", "ease_in_out_cubic"))
    if interpolation not in TRAJECTORY_EASE_WHITELIST:
        allowed = ", ".join(sorted(TRAJECTORY_EASE_WHITELIST))
        raise SkillDefinitionError(
            "invalid_trajectory",
            f"step[{idx}].trajectory.interpolation '{interpolation}' not allowed; choose one of: {allowed}",
        )

    ease_overshoot = traj.get("ease_overshoot", 0.10)
    try:
        ease_overshoot = float(ease_overshoot)
    except (TypeError, ValueError) as exc:
        raise SkillDefinitionError(
            "invalid_trajectory",
            f"step[{idx}].trajectory.ease_overshoot must be a number",
        ) from exc
    if not (0.0 <= ease_overshoot <= 0.5):
        raise SkillDefinitionError(
            "invalid_trajectory",
            f"step[{idx}].trajectory.ease_overshoot must be in [0.0, 0.5]",
        )

    normalised_wps: list[dict[str, Any]] = []
    total_duration = 0.0
    allowed_joints = set(JOINT_NAMES)
    for wp_idx, wp in enumerate(waypoints):
        if not isinstance(wp, dict):
            raise SkillDefinitionError(
                "invalid_trajectory",
                f"step[{idx}].trajectory.waypoints[{wp_idx}] must be an object",
            )
        joints_raw = wp.get("joints")
        if not isinstance(joints_raw, dict) or not joints_raw:
            raise SkillDefinitionError(
                "invalid_trajectory",
                f"step[{idx}].trajectory.waypoints[{wp_idx}].joints must be a non-empty object",
            )
        joints: dict[str, float] = {}
        for joint_name, raw_val in joints_raw.items():
            if joint_name not in allowed_joints:
                raise SkillDefinitionError(
                    "invalid_joint",
                    (
                        f"step[{idx}].trajectory.waypoints[{wp_idx}].joints['{joint_name}'] "
                        f"is not a valid joint; allowed: {sorted(allowed_joints)}"
                    ),
                )
            try:
                val = float(raw_val)
            except (TypeError, ValueError) as exc:
                raise SkillDefinitionError(
                    "invalid_joint_value",
                    (
                        f"step[{idx}].trajectory.waypoints[{wp_idx}].joints['{joint_name}'] "
                        f"must be a number, got {raw_val!r}"
                    ),
                ) from exc
            # Bound-check against the hardware-mechanical limits table.
            # These are the *conservative* defaults; the running SafetyKernel
            # may be configured even tighter, in which case the SafetyKernel
            # will additionally clamp at stream time — defence in depth.
            lim = DEFAULT_JOINT_LIMITS.get(joint_name)
            if lim is not None and not (lim.min <= val <= lim.max):
                raise SkillDefinitionError(
                    "joint_out_of_range",
                    (
                        f"step[{idx}].trajectory.waypoints[{wp_idx}].joints['{joint_name}'] = "
                        f"{val} is outside the safe range [{lim.min}, {lim.max}]"
                    ),
                )
            joints[joint_name] = val

        duration = wp.get("duration", 0.0)
        try:
            duration = float(duration)
        except (TypeError, ValueError) as exc:
            raise SkillDefinitionError(
                "invalid_trajectory",
                f"step[{idx}].trajectory.waypoints[{wp_idx}].duration must be a number",
            ) from exc
        if duration < 0:
            raise SkillDefinitionError(
                "invalid_trajectory",
                f"step[{idx}].trajectory.waypoints[{wp_idx}].duration must be ≥ 0",
            )
        # First waypoint's duration is ignored by the generator (it's the
        # starting pose, not a segment target).  We still require ≥ 0 so a
        # typo doesn't sneak through, but we don't add it to total_duration.
        if wp_idx > 0:
            total_duration += duration

        normalised_wps.append({"joints": joints, "duration": duration})

    if total_duration > MAX_TRAJECTORY_DURATION_S:
        raise SkillDefinitionError(
            "trajectory_too_long",
            (
                f"step[{idx}].trajectory total segment duration {total_duration:.2f}s "
                f"exceeds cap {MAX_TRAJECTORY_DURATION_S}s"
            ),
        )

    return {
        "trajectory": {
            "waypoints": normalised_wps,
            "fps": fps,
            "interpolation": interpolation,
            "ease_overshoot": ease_overshoot,
        }
    }


def validate_definition(
    definition: Any,
    *,
    registry: SkillRegistry,
    existing_user_skills: set[str] | None = None,
) -> dict[str, Any]:
    """Validate a raw dict (as parsed from JSON) and return a normalised copy.

    Parameters
    ----------
    definition:
        Must be a dict; anything else raises.
    registry:
        Used to (a) confirm every step target exists and (b) prevent shadowing
        factory skills.
    existing_user_skills:
        Optional set of already-registered user skill ids.  Passed when
        deciding whether a ``save`` call is an *update* (id present) or a
        *collision* (id refers to a factory skill).
    """
    if not isinstance(definition, dict):
        raise SkillDefinitionError(
            "invalid_payload", "skill definition must be a JSON object"
        )

    skill_id = str(definition.get("skill_id", "") or "").strip()
    if not SKILL_ID_PATTERN.match(skill_id):
        raise SkillDefinitionError(
            "invalid_skill_id",
            "skill_id must match ^[a-z][a-z0-9_]{0,63}$ (lowercase, digits, underscore)",
        )

    # factory-skill collision: the registered skill carries source="factory"
    # by default.  We look it up rather than maintaining a parallel list so
    # that future factory additions are automatically off-limits.
    collided = registry.get(skill_id)
    existing = existing_user_skills or set()
    if collided is not None and getattr(collided, "source", "factory") == "factory":
        raise SkillDefinitionError(
            "duplicate_skill",
            f"skill_id '{skill_id}' is reserved by a factory skill and cannot be overridden",
        )

    description = str(definition.get("description", "") or "").strip()
    if not description:
        raise SkillDefinitionError(
            "missing_description",
            "description is required (one line summary surfaced to LLM/UI)",
        )

    steps = definition.get("steps")
    if not isinstance(steps, list) or not steps:
        raise SkillDefinitionError(
            "missing_steps", "steps must be a non-empty array"
        )
    if len(steps) > MAX_STEPS_PER_SKILL:
        raise SkillDefinitionError(
            "too_many_steps",
            f"steps cannot exceed {MAX_STEPS_PER_SKILL} (got {len(steps)})",
        )

    normalised_steps: list[dict[str, Any]] = []
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            raise SkillDefinitionError(
                "invalid_step", f"step[{idx}] must be an object"
            )
        # Dispatch on the discriminator key.  Accept exactly one of the two
        # shapes; rejecting "both present" keeps the JSON unambiguous for
        # downstream tooling (UI summary and agent introspection).
        has_skill = bool(str(step.get("skill_id", "") or "").strip())
        has_traj = isinstance(step.get("trajectory"), dict)
        if has_skill and has_traj:
            raise SkillDefinitionError(
                "invalid_step",
                f"step[{idx}] has both 'skill_id' and 'trajectory' — pick one shape per step",
            )
        if not has_skill and not has_traj:
            raise SkillDefinitionError(
                "invalid_step",
                f"step[{idx}] must have either 'skill_id' (factory call) or 'trajectory' (custom path)",
            )

        if has_skill:
            normalised_steps.append(
                _validate_skill_step(step, idx, registry=registry)
            )
        else:
            normalised_steps.append(
                _validate_trajectory_step(step, idx)
            )

    # Parameters block is optional; if present it must be object-of-object.
    raw_params = definition.get("parameters") or {}
    if not isinstance(raw_params, dict):
        raise SkillDefinitionError(
            "invalid_parameters",
            "parameters must be an object (name -> {type, description, required?, default?})",
        )
    normalised_params: dict[str, dict[str, Any]] = {}
    for name, spec in raw_params.items():
        if not isinstance(spec, dict):
            raise SkillDefinitionError(
                "invalid_parameters",
                f"parameters['{name}'] must be an object",
            )
        ptype = str(spec.get("type", "str")).strip().lower()
        if ptype not in {"str", "string", "int", "integer", "float", "number", "bool", "boolean"}:
            raise SkillDefinitionError(
                "invalid_parameters",
                f"parameters['{name}'].type must be one of str/int/float/bool",
            )
        normalised_params[name] = {
            "type": ptype,
            "description": str(spec.get("description", "")),
            "required": bool(spec.get("required", False)),
            "default": spec.get("default"),
        }

    label = str(definition.get("label", "") or "").strip()

    return {
        "skill_id": skill_id,
        "label": label,
        "description": description,
        "parameters": normalised_params,
        "steps": normalised_steps,
        "source": "user",
        # Pass-through metadata for audit trails — not validated.
        "author": str(definition.get("author", "") or ""),
        "created_at": str(definition.get("created_at", "") or ""),
        "updated_at": str(definition.get("updated_at", "") or ""),
        "is_used_by_existing": skill_id in existing,
    }


def load_user_skills(
    directory: Path,
    registry: SkillRegistry,
) -> LoadReport:
    """Scan ``directory`` and register every valid JSON definition.

    Malformed files are skipped with a warning — one bad definition cannot
    take the rest offline.  The report is returned so callers can surface
    errors to the user (Web UI, CLI).
    """
    loaded: list[str] = []
    errors: list[tuple[str, str]] = []

    if not directory.exists():
        return LoadReport(loaded=loaded, errors=errors)

    existing_user_ids = {
        s.skill_id
        for s in registry.list_skills()
        if getattr(s, "source", "factory") == "user"
    }

    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append((str(path), f"JSON parse failed: {exc}"))
            logger.warning("loader.parse_failed", path=str(path), error=str(exc))
            continue

        try:
            definition = validate_definition(
                raw,
                registry=registry,
                existing_user_skills=existing_user_ids,
            )
        except SkillDefinitionError as exc:
            errors.append((str(path), f"{exc.reason}: {exc}"))
            logger.warning(
                "loader.validation_failed",
                path=str(path),
                reason=exc.reason,
                error=str(exc),
            )
            continue

        skill = ComposedSkill(definition, registry)
        registry.register(skill)
        existing_user_ids.add(skill.skill_id)
        loaded.append(skill.skill_id)
        logger.info(
            "loader.registered_user_skill",
            skill_id=skill.skill_id,
            steps=len(definition["steps"]),
            path=str(path),
        )

    return LoadReport(loaded=loaded, errors=errors)


def save_user_skill(
    directory: Path,
    definition: dict[str, Any],
) -> Path:
    """Write a validated definition to disk.  Caller must have already
    run :func:`validate_definition`.  Returns the file path.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{definition['skill_id']}.json"
    payload = {
        k: v
        for k, v in definition.items()
        if k not in {"is_used_by_existing", "source"}
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def delete_user_skill(directory: Path, skill_id: str) -> bool:
    """Remove ``<skill_id>.json`` from disk.  Returns True if a file was
    deleted, False if it didn't exist.  Caller is responsible for also
    removing the skill from the in-memory registry.
    """
    path = directory / f"{skill_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True
