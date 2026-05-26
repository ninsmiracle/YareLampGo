"""Tests for the JSON user-skill loader and validator.

Scope: pure in-memory validation + disk IO.  These tests deliberately do
NOT spin up a full server — that's covered by ``test_composed_skill.py``
and (eventually) the gateway integration tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lampgo.skills.base import ParameterSpec, Skill, SkillContext
from lampgo.skills.composed import MAX_STEPS_PER_SKILL, ComposedSkill
from lampgo.skills.loader import (
    MAX_TRAJECTORY_DURATION_S,
    MAX_WAYPOINTS_PER_TRAJECTORY,
    SkillDefinitionError,
    delete_user_skill,
    load_user_skills,
    save_user_skill,
    validate_definition,
)
from lampgo.skills.registry import SkillRegistry


class _FakeFactorySkill(Skill):
    """Lightweight stand-in for a factory skill with zero dependencies."""

    source = "factory"

    def __init__(self, skill_id: str) -> None:
        self.skill_id = skill_id
        self.description = f"fake factory skill {skill_id}"
        self.parameters = {}

    async def execute(self, ctx: SkillContext, **params):  # type: ignore[override]
        raise NotImplementedError


def _registry_with(*skill_ids: str) -> SkillRegistry:
    reg = SkillRegistry()
    for sid in skill_ids:
        reg.register(_FakeFactorySkill(sid))
    return reg


# ---------- happy path -----------------------------------------------------

def test_validate_minimal_definition_normalises_and_defaults():
    reg = _registry_with("move_to", "nod")
    raw = {
        "skill_id": "welcome",
        "description": "welcome dance",
        "steps": [
            {"skill_id": "move_to", "params": {"base_yaw": 0}},
            {"skill_id": "nod"},
        ],
    }
    out = validate_definition(raw, registry=reg)
    assert out["skill_id"] == "welcome"
    assert out["source"] == "user"
    # Missing step.params should normalise to an empty dict, not None —
    # ComposedSkill.execute assumes dict-shaped params.
    assert out["steps"][1]["params"] == {}


def test_save_and_load_roundtrip(tmp_path: Path):
    reg = _registry_with("nod", "set_expression")
    definition = validate_definition(
        {
            "skill_id": "cheer",
            "label": "欢呼",
            "description": "Nod twice while smiling.",
            "parameters": {
                "count": {"type": "int", "default": 2, "description": "nods"},
            },
            "steps": [
                {"skill_id": "set_expression", "params": {"expression": "smiley"}},
                {"skill_id": "nod", "params": {"count": "{count}"}},
            ],
        },
        registry=reg,
    )
    path = save_user_skill(tmp_path, definition)
    assert path.exists()
    assert path.name == "cheer.json"

    # Persisted file must NOT carry internal-only fields.
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert "is_used_by_existing" not in disk
    assert "source" not in disk
    assert disk["label"] == "欢呼"

    report = load_user_skills(tmp_path, reg)
    assert report.loaded == ["cheer"]
    assert report.errors == []

    loaded = reg.get("cheer")
    assert isinstance(loaded, ComposedSkill)
    assert loaded.source == "user"
    assert loaded.label == "欢呼"
    assert "count" in loaded.parameters


def test_delete_user_skill_removes_file(tmp_path: Path):
    (tmp_path / "foo.json").write_text("{}", encoding="utf-8")
    assert delete_user_skill(tmp_path, "foo") is True
    assert not (tmp_path / "foo.json").exists()
    # Second delete is a no-op — callers use the False return to decide
    # whether to surface a "not found" error.
    assert delete_user_skill(tmp_path, "foo") is False


# ---------- validation failures -------------------------------------------

@pytest.mark.parametrize(
    "bad_id",
    ["", "Welcome", "welcome!", "9start", "a" * 65],
)
def test_reject_invalid_skill_id(bad_id: str):
    reg = _registry_with("nod")
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {
                "skill_id": bad_id,
                "description": "x",
                "steps": [{"skill_id": "nod"}],
            },
            registry=reg,
        )
    assert ei.value.reason == "invalid_skill_id"


def test_reject_factory_id_collision():
    reg = _registry_with("nod")
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {"skill_id": "nod", "description": "x", "steps": [{"skill_id": "nod"}]},
            registry=reg,
        )
    assert ei.value.reason == "duplicate_skill"


def test_reject_estop_step():
    reg = _registry_with("estop", "nod")
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {
                "skill_id": "bad",
                "description": "x",
                "steps": [{"skill_id": "estop"}],
            },
            registry=reg,
        )
    assert ei.value.reason == "forbidden_step"


def test_reject_composed_calling_composed():
    reg = _registry_with("nod")
    # Pre-register a user skill to simulate prior load.
    reg.register(
        ComposedSkill(
            {
                "skill_id": "first",
                "description": "x",
                "steps": [{"skill_id": "nod", "params": {}}],
                "parameters": {},
            },
            reg,
        )
    )
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {
                "skill_id": "second",
                "description": "x",
                "steps": [{"skill_id": "first"}],
            },
            registry=reg,
        )
    assert ei.value.reason == "composed_step_forbidden"


def test_reject_unknown_step_target():
    reg = _registry_with("nod")
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {
                "skill_id": "oops",
                "description": "x",
                "steps": [{"skill_id": "does_not_exist"}],
            },
            registry=reg,
        )
    assert ei.value.reason == "unknown_step_skill"


def test_reject_too_many_steps():
    reg = _registry_with("nod")
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {
                "skill_id": "huge",
                "description": "x",
                "steps": [{"skill_id": "nod"}] * (MAX_STEPS_PER_SKILL + 1),
            },
            registry=reg,
        )
    assert ei.value.reason == "too_many_steps"


def test_reject_missing_description():
    reg = _registry_with("nod")
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {"skill_id": "x", "steps": [{"skill_id": "nod"}]},
            registry=reg,
        )
    assert ei.value.reason == "missing_description"


def test_loader_skips_bad_files_and_continues(tmp_path: Path):
    """One bad JSON mustn't take the whole batch offline."""
    reg = _registry_with("nod")
    (tmp_path / "good.json").write_text(
        json.dumps(
            {
                "skill_id": "good",
                "description": "ok",
                "steps": [{"skill_id": "nod"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "invalid.json").write_text(
        json.dumps({"skill_id": "invalid"}),  # missing description/steps
        encoding="utf-8",
    )

    report = load_user_skills(tmp_path, reg)
    assert "good" in report.loaded
    assert len(report.errors) == 2
    assert reg.get("good") is not None
    assert reg.get("invalid") is None


# ---------- Level 2 trajectory validation ---------------------------------


def _valid_trajectory_step() -> dict:
    """Canonical well-formed trajectory step, used as the starting point for
    negative-path tests — each test mutates exactly one field so the
    assertion pins the intended failure, not incidental drift."""
    return {
        "trajectory": {
            "waypoints": [
                {"joints": {"base_yaw": 0.0, "base_pitch": -38.0}, "duration": 0.0},
                {"joints": {"base_yaw": 30.0}, "duration": 0.4},
                {"joints": {"base_yaw": -30.0}, "duration": 0.4},
                {"joints": {"base_yaw": 0.0, "base_pitch": -38.0}, "duration": 0.3},
            ],
            "fps": 50,
            "interpolation": "ease_in_out_cubic",
        }
    }


def test_trajectory_step_happy_path():
    reg = _registry_with("nod")
    out = validate_definition(
        {
            "skill_id": "wiggle",
            "description": "side-to-side wiggle",
            "steps": [_valid_trajectory_step()],
        },
        registry=reg,
    )
    step = out["steps"][0]
    assert "trajectory" in step
    # Durations normalised to floats; joints preserved.
    assert step["trajectory"]["fps"] == 50
    assert step["trajectory"]["waypoints"][1]["duration"] == pytest.approx(0.4)


def test_trajectory_and_skill_id_mutually_exclusive_per_step():
    reg = _registry_with("nod")
    step = _valid_trajectory_step()
    step["skill_id"] = "nod"
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {"skill_id": "mix", "description": "x", "steps": [step]},
            registry=reg,
        )
    assert ei.value.reason == "invalid_step"


def test_trajectory_waypoint_joint_out_of_range():
    reg = _registry_with("nod")
    step = _valid_trajectory_step()
    step["trajectory"]["waypoints"][1]["joints"]["base_yaw"] = 999.0
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {"skill_id": "boom", "description": "x", "steps": [step]},
            registry=reg,
        )
    assert ei.value.reason == "joint_out_of_range"


def test_trajectory_unknown_joint_name():
    reg = _registry_with("nod")
    step = _valid_trajectory_step()
    step["trajectory"]["waypoints"][1]["joints"]["not_a_joint"] = 10.0
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {"skill_id": "bad", "description": "x", "steps": [step]},
            registry=reg,
        )
    assert ei.value.reason == "invalid_joint"


@pytest.mark.parametrize("bad_fps", [0, 5, 500, -1])
def test_trajectory_fps_must_be_in_range(bad_fps: int):
    reg = _registry_with("nod")
    step = _valid_trajectory_step()
    step["trajectory"]["fps"] = bad_fps
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {"skill_id": "fps_bad", "description": "x", "steps": [step]},
            registry=reg,
        )
    assert ei.value.reason == "invalid_trajectory"


def test_trajectory_interpolation_must_be_whitelisted():
    reg = _registry_with("nod")
    step = _valid_trajectory_step()
    step["trajectory"]["interpolation"] = "exec('rm -rf /')"
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {"skill_id": "evil", "description": "x", "steps": [step]},
            registry=reg,
        )
    assert ei.value.reason == "invalid_trajectory"


def test_trajectory_too_many_waypoints():
    reg = _registry_with("nod")
    step = _valid_trajectory_step()
    # Generate (cap + 2) waypoints — one over the explicit cap.
    step["trajectory"]["waypoints"] = [
        {"joints": {"base_yaw": 0.0}, "duration": 0.05}
        for _ in range(MAX_WAYPOINTS_PER_TRAJECTORY + 2)
    ]
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {"skill_id": "huge", "description": "x", "steps": [step]},
            registry=reg,
        )
    assert ei.value.reason == "too_many_waypoints"


def test_trajectory_total_duration_cap():
    reg = _registry_with("nod")
    step = _valid_trajectory_step()
    # Blow past the 30 s total cap with two long segments.
    step["trajectory"]["waypoints"] = [
        {"joints": {"base_yaw": 0.0}, "duration": 0.0},
        {"joints": {"base_yaw": 10.0}, "duration": MAX_TRAJECTORY_DURATION_S},
        {"joints": {"base_yaw": 0.0}, "duration": 1.0},
    ]
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {"skill_id": "long", "description": "x", "steps": [step]},
            registry=reg,
        )
    assert ei.value.reason == "trajectory_too_long"


def test_trajectory_requires_at_least_two_waypoints():
    reg = _registry_with("nod")
    step = _valid_trajectory_step()
    step["trajectory"]["waypoints"] = [{"joints": {"base_yaw": 0.0}, "duration": 0.0}]
    with pytest.raises(SkillDefinitionError) as ei:
        validate_definition(
            {"skill_id": "single", "description": "x", "steps": [step]},
            registry=reg,
        )
    assert ei.value.reason == "invalid_trajectory"


def test_mixed_skill_and_trajectory_steps_are_allowed():
    """The whole point of Level 2 is intercalating custom trajectories with
    factory skills — a hybrid skill must validate cleanly."""
    reg = _registry_with("nod", "set_expression")
    out = validate_definition(
        {
            "skill_id": "hybrid",
            "description": "mixed shape",
            "steps": [
                {"skill_id": "set_expression", "params": {"expression": "smiley"}},
                _valid_trajectory_step(),
                {"skill_id": "nod", "params": {"count": 1}},
            ],
        },
        registry=reg,
    )
    assert len(out["steps"]) == 3
    assert "skill_id" in out["steps"][0]
    assert "trajectory" in out["steps"][1]
    assert "skill_id" in out["steps"][2]


# ---------- ParameterSpec round-trip --------------------------------------

def test_parameter_spec_round_trip_on_composed_skill():
    reg = _registry_with("nod")
    definition = validate_definition(
        {
            "skill_id": "pspec_test",
            "description": "x",
            "parameters": {
                "amp": {"type": "float", "required": True, "description": "amplitude"},
                "name": {"type": "str", "default": "anon"},
            },
            "steps": [{"skill_id": "nod"}],
        },
        registry=reg,
    )
    skill = ComposedSkill(definition, reg)
    assert isinstance(skill.parameters["amp"], ParameterSpec)
    assert skill.parameters["amp"].required is True
    assert skill.parameters["name"].default == "anon"
