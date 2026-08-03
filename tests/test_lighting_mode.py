from __future__ import annotations

import asyncio
import csv
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from lampgo.core.hal import MotorStartupState
from lampgo.core.types import JOINT_NAMES, InvokeResult, JointState
from lampgo.lighting_mode import LightingModeController, load_taught_lighting_pose
from lampgo.server import LampgoServer
from lampgo.skills.builtin.lighting_mode_skills import (
    EnterLightingModeSkill,
    ExitLightingModeSkill,
)

TARGET_POSE = {
    "base_yaw": 1.1,
    "base_pitch": -2.24,
    "elbow_pitch": 10.55,
    "wrist_roll": 55.91,
    "wrist_pitch": 2.42,
}


def _write_recording(path: Path, *, unstable_tail: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["timestamp", *(f"{joint}.pos" for joint in JOINT_NAMES)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index in range(120):
            settled = index >= 80
            row = {"timestamp": index / 10.0}
            for joint_index, joint in enumerate(JOINT_NAMES):
                if settled:
                    value = TARGET_POSE[joint]
                    if unstable_tail and joint == "wrist_roll":
                        value += (index - 80) * 0.1
                else:
                    value = TARGET_POSE[joint] + 8.0 - index * 0.1 + joint_index
                row[f"{joint}.pos"] = value
            writer.writerow(row)


def test_taught_pose_uses_stable_tail_instead_of_replaying_trajectory(tmp_path: Path) -> None:
    source = tmp_path / "user" / "照明模式.csv"
    _write_recording(source)

    taught = load_taught_lighting_pose(tmp_path)

    assert taught.source_path == source
    assert taught.source_frames == 120
    assert taught.settle_frames >= 30
    assert taught.joints == pytest.approx(TARGET_POSE)


def test_taught_pose_rejects_a_recording_that_is_still_moving(tmp_path: Path) -> None:
    _write_recording(tmp_path / "user" / "照明模式.csv", unstable_tail=True)

    with pytest.raises(ValueError, match="末段仍在移动.*wrist_roll"):
        load_taught_lighting_pose(tmp_path)


class _Motion:
    is_virtual = False

    def __init__(
        self,
        *,
        running: bool = True,
        position_offsets: dict[str, float] | None = None,
    ) -> None:
        self.is_running = running
        self.targets = []
        self.current_state = JointState({joint: 0.0 for joint in JOINT_NAMES})
        self.stopped = False
        self.start_calls = 0
        self.recovery_error = None
        self.position_offsets = dict(position_offsets or {})

    def start(self) -> None:
        self.start_calls += 1
        self.is_running = True

    def move_to(self, target):
        self.targets.append(target)
        self.current_state = JointState(
            {
                joint: value + self.position_offsets.get(joint, 0.0)
                for joint, value in target.joints.items()
            }
        )
        done = threading.Event()
        done.set()
        return done

    def stop_immediate(self) -> None:
        self.stopped = True


class _Led:
    is_connected = True

    def __init__(self) -> None:
        self.brightness = []
        self.modes = []
        self.off_calls = 0

    def set_brightness(self, level: int) -> bool:
        self.brightness.append(level)
        return True

    def set_mode(self, mode: str) -> bool:
        self.modes.append(mode)
        return True

    def off(self) -> bool:
        self.off_calls += 1
        return True


class _Safety:
    @staticmethod
    def is_estopped() -> bool:
        return False


class _Hal:
    is_connected = True
    recovery_required = False
    recovery_reason = None
    startup_state = MotorStartupState.READY


class _BackgroundEffect:
    def __init__(self) -> None:
        self.deactivate_calls = 0

    def deactivate(self) -> None:
        self.deactivate_calls += 1


@pytest.mark.asyncio
async def test_enter_moves_once_then_holds_white_light(tmp_path: Path) -> None:
    _write_recording(tmp_path / "user" / "照明模式.csv")
    motion = _Motion()
    led = _Led()
    clock = _BackgroundEffect()
    ocean = _BackgroundEffect()
    controller = LightingModeController(
        recordings_dir=tmp_path,
        motion=motion,
        led=led,
        safety=_Safety(),
        hal=_Hal(),
        clock=clock,
        electronic_ocean=ocean,
        brightness=lambda: 120,
        no_hw=lambda: False,
    )

    result = await controller.enter()
    repeated = await controller.enter()

    assert result.status == "ok"
    assert repeated.status == "ok"
    assert repeated.data["already_active"] is True
    assert controller.is_active is True
    assert len(motion.targets) == 1
    assert motion.targets[0].joints == pytest.approx(TARGET_POSE)
    assert motion.targets[0].max_velocity == 30.0
    assert motion.targets[0].anticipation is False
    assert led.brightness == [96]
    assert led.modes == ["white"]
    assert clock.deactivate_calls == 1
    assert ocean.deactivate_calls == 1


@pytest.mark.asyncio
async def test_video_pose_accepts_a_four_degree_loaded_elbow_residual(tmp_path: Path) -> None:
    _write_recording(tmp_path / "user" / "照明模式.csv")
    motion = _Motion(position_offsets={"elbow_pitch": 4.13})
    led = _Led()
    controller = LightingModeController(
        recordings_dir=tmp_path,
        motion=motion,
        led=led,
        safety=_Safety(),
        hal=_Hal(),
        clock=None,
        electronic_ocean=None,
        brightness=lambda: 32,
        no_hw=lambda: False,
    )

    result = await controller.enter()

    assert result.status == "ok"
    assert result.data["pose_warning"] == {"elbow_pitch": 4.13}
    assert controller.is_active is True
    assert led.modes == ["white"]


@pytest.mark.asyncio
async def test_video_pose_still_rejects_a_clearly_wrong_pose(tmp_path: Path) -> None:
    _write_recording(tmp_path / "user" / "照明模式.csv")
    motion = _Motion(position_offsets={"elbow_pitch": 8.01})
    controller = LightingModeController(
        recordings_dir=tmp_path,
        motion=motion,
        led=_Led(),
        safety=_Safety(),
        hal=_Hal(),
        clock=None,
        electronic_ocean=None,
        brightness=lambda: 32,
        no_hw=lambda: False,
    )

    result = await controller.enter()

    assert result.status == "error"
    assert "明显偏离照明姿态" in result.message
    assert result.data["pose_errors"] == {"elbow_pitch": 8.01}


@pytest.mark.asyncio
async def test_enter_repairs_a_stopped_runtime_when_hal_is_ready(tmp_path: Path) -> None:
    _write_recording(tmp_path / "user" / "照明模式.csv")
    motion = _Motion(running=False)
    controller = LightingModeController(
        recordings_dir=tmp_path,
        motion=motion,
        led=_Led(),
        safety=_Safety(),
        hal=_Hal(),
        clock=None,
        electronic_ocean=None,
        brightness=lambda: 32,
        no_hw=lambda: False,
    )

    result = await controller.enter()

    assert result.status == "ok"
    assert motion.start_calls == 1
    assert len(motion.targets) == 1


@pytest.mark.asyncio
async def test_enter_does_not_bypass_an_incomplete_recovery(tmp_path: Path) -> None:
    _write_recording(tmp_path / "user" / "照明模式.csv")
    motion = _Motion(running=False)
    motion.recovery_error = "elbow_pitch recovery stalled"
    hal = _Hal()
    hal.startup_state = MotorStartupState.RECOVERING
    controller = LightingModeController(
        recordings_dir=tmp_path,
        motion=motion,
        led=_Led(),
        safety=_Safety(),
        hal=hal,
        clock=None,
        electronic_ocean=None,
        brightness=lambda: 32,
        no_hw=lambda: False,
    )

    result = await controller.enter()

    assert result.status == "error"
    assert result.message == "elbow_pitch recovery stalled"
    assert motion.start_calls == 0
    assert motion.targets == []


class _LightingState:
    def __init__(self) -> None:
        self.state = "active"
        self.begin_exit_calls = 0
        self.finish_exit_calls = 0

    @property
    def is_engaged(self) -> bool:
        return self.state in {"entering", "active", "exiting"}

    async def begin_exit(self, *, reason: str, force: bool = False) -> bool:
        del reason, force
        if not self.is_engaged:
            return False
        self.begin_exit_calls += 1
        self.state = "exiting"
        return True

    async def finish_exit(self, *, return_safe_status: str, error: str = "") -> None:
        del return_safe_status, error
        self.finish_exit_calls += 1
        self.state = "normal"

    def snapshot(self) -> dict:
        return {"state": self.state, "active": self.state == "active"}


class _Executor:
    def __init__(self) -> None:
        self.invocations = []
        self.cancel_calls = 0

    async def cancel_current(self) -> None:
        self.cancel_calls += 1

    async def invoke(self, skill_id: str, _ctx, **params) -> InvokeResult:
        self.invocations.append((skill_id, params))
        await asyncio.sleep(0)
        return InvokeResult(
            invocation_id=f"inv-{len(self.invocations)}",
            status="ok",
            result={"skill_id": skill_id},
        )


def _server_shell() -> LampgoServer:
    server = object.__new__(LampgoServer)
    server._lighting_entry_lock = asyncio.Lock()
    server._lighting_exit_lock = asyncio.Lock()
    server.lighting_mode = _LightingState()
    server.executor = _Executor()
    server.hal = SimpleNamespace(
        startup_state=MotorStartupState.READY,
        recovery_reason=None,
    )
    server.motion = SimpleNamespace(recovery_error=None)
    server.make_context = lambda: object()
    return server


@pytest.mark.asyncio
async def test_explicit_exit_runs_standalone_return_safe_exactly_once() -> None:
    server = _server_shell()

    first, second = await asyncio.gather(
        server._exit_lighting_mode_with_return_safe(reason="llm_exit"),
        server._exit_lighting_mode_with_return_safe(reason="duplicate_exit"),
    )

    assert [skill_id for skill_id, _params in server.executor.invocations] == ["return_safe"]
    assert server.executor.invocations[0][1] == {"velocity": 60.0}
    assert server.lighting_mode.begin_exit_calls == 1
    assert server.lighting_mode.finish_exit_calls == 1
    assert first.result["return_safe_invoked"] is True
    assert second.result["return_safe_invoked"] is False


@pytest.mark.asyncio
async def test_llm_tool_operation_preempts_mode_before_running_requested_skill() -> None:
    server = _server_shell()

    result = await server._invoke_lampgo_skill(
        "nod",
        object(),
        reason="agent_tool:nod",
    )

    assert result.status == "ok"
    assert [skill_id for skill_id, _params in server.executor.invocations] == [
        "return_safe",
        "nod",
    ]


@pytest.mark.asyncio
async def test_lighting_entry_runs_recovery_first_when_required() -> None:
    server = _server_shell()
    server.hal.startup_state = MotorStartupState.RECOVERY_REQUIRED

    result = await server._invoke_lampgo_skill(
        "enter_lighting_mode",
        object(),
        reason="agent_tool:enter_lighting_mode",
    )

    assert result.status == "ok"
    assert [skill_id for skill_id, _params in server.executor.invocations] == [
        "return_safe",
        "enter_lighting_mode",
    ]
    assert server.executor.invocations[0][1] == {"velocity": 30.0}


@pytest.mark.asyncio
async def test_lighting_entry_reports_incomplete_recovery_instead_of_runtime_error() -> None:
    server = _server_shell()
    server.hal.startup_state = MotorStartupState.RECOVERING
    server.motion.recovery_error = "base_pitch recovery stalled"

    result = await server._invoke_lampgo_skill(
        "enter_lighting_mode",
        object(),
        reason="agent_tool:enter_lighting_mode",
    )

    assert result.status == "rejected"
    assert result.error_code == "motor_recovery_incomplete"
    assert result.error_detail == "base_pitch recovery stalled"
    assert server.executor.invocations == []


@pytest.mark.asyncio
async def test_concurrent_lighting_entries_are_serialized() -> None:
    class BlockingEntryExecutor(_Executor):
        def __init__(self) -> None:
            super().__init__()
            self.active_entries = 0
            self.max_active_entries = 0

        async def invoke(self, skill_id: str, _ctx, **params) -> InvokeResult:
            self.invocations.append((skill_id, params))
            if skill_id == "enter_lighting_mode":
                self.active_entries += 1
                self.max_active_entries = max(self.max_active_entries, self.active_entries)
                await asyncio.sleep(0.01)
                self.active_entries -= 1
            return InvokeResult(
                invocation_id=f"inv-{len(self.invocations)}",
                status="ok",
                result={"skill_id": skill_id},
            )

    server = _server_shell()
    server.executor = BlockingEntryExecutor()

    first, second = await asyncio.gather(
        server._invoke_lampgo_skill(
            "enter_lighting_mode",
            object(),
            reason="first_enter",
        ),
        server._invoke_lampgo_skill(
            "enter_lighting_mode",
            object(),
            reason="duplicate_enter",
        ),
    )

    assert first.status == "ok"
    assert second.status == "ok"
    assert server.executor.max_active_entries == 1
    assert [skill_id for skill_id, _params in server.executor.invocations] == [
        "enter_lighting_mode",
        "enter_lighting_mode",
    ]


def test_lighting_tools_tell_the_llm_when_to_enter_and_exit() -> None:
    assert "用户明确要求启动或进入照明模式" in EnterLightingModeSkill.description
    assert "用户明确要求退出或关闭照明模式" in ExitLightingModeSkill.description
    assert "只执行一次 return_safe" in ExitLightingModeSkill.description
