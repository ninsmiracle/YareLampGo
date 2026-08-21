"""CLI utility command tests."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from types import SimpleNamespace

import pytest

from lampgo import cli


def test_build_help_text_contains_common_commands():
    text = cli._build_help_text()
    assert "uv run lampgo help" in text
    assert "uv run lampgo run --web" in text
    assert "uv run lampgo run --web --no-hw" in text
    assert "http://127.0.0.1:8420" in text
    assert "uv run lampgo detect" in text
    assert "uv run lampgo scan-motors --ids 1-5" in text
    assert "uv run lampgo ping" in text
    assert "uv run lampgo clear" in text
    assert "uv run lampgo setup-motors" in text
    assert "uv run lampgo calibrate" in text
    assert "uv run lampgo text" in text
    assert "uv run lampgo invoke" in text
    assert "uv run lampgo move" in text
    assert "uv run lampgo record" in text
    assert "uv run lampgo play" in text
    assert "uv run lampgo <command> --help" in text


def test_find_related_pids_posix_filters_self_and_parent(monkeypatch):
    monkeypatch.setattr(cli.os, "getpid", lambda: 100)
    monkeypatch.setattr(cli.os, "getppid", lambda: 99)
    fake_ps = SimpleNamespace(
        stdout=(
            "100 uv run lampgo clear\n"
            "99 /bin/zsh -c uv run lampgo clear\n"
            "200 uv run lampgo run\n"
            "201 codex exec unrelated-task\n"
            "202 python something_else.py\n"
        )
    )
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: fake_ps)

    pids = cli._find_related_pids_posix()
    assert pids == [200]


def test_find_related_pids_windows_does_not_fall_back_to_posix(monkeypatch):
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(cli, "_find_related_pids_windows", lambda: [])
    monkeypatch.setattr(
        cli,
        "_find_related_pids_posix",
        lambda: (_ for _ in ()).throw(AssertionError("POSIX fallback must not run on Windows")),
    )

    assert cli._find_related_pids() == []


def test_find_related_pids_windows_warns_when_psutil_is_missing(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "psutil", None)

    assert cli._find_related_pids_windows() == []
    assert "requires psutil" in capsys.readouterr().err


def test_terminate_pids_windows_waits_after_force_kill(monkeypatch):
    events: list[str] = []

    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    class Process:
        pid = 200

        def terminate(self):
            events.append("terminate")

        def kill(self):
            events.append("kill")

    process = Process()
    waits = iter([
        ([], [process]),
        ([process], []),
    ])
    fake_psutil = SimpleNamespace(
        NoSuchProcess=NoSuchProcess,
        AccessDenied=AccessDenied,
        Process=lambda _pid: process,
        wait_procs=lambda processes, timeout: next(waits),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert cli._terminate_pids_windows([process.pid]) == ([process.pid], [])
    assert events == ["terminate", "kill"]


def test_cmd_clear_skips_torque_release_when_processes_remain(monkeypatch, capsys):
    args = argparse.Namespace(config=None, skip_kill=False, skip_release=False)

    import lampgo.core.config as config_mod
    import lampgo.ipc as ipc

    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda config_path=None: SimpleNamespace(
            socket_path="tcp://127.0.0.1:28420",
            device=SimpleNamespace(motor_port="COM5", lamp_id="AL02"),
        ),
    )
    monkeypatch.setattr(cli, "_find_related_pids", lambda: [200])
    monkeypatch.setattr(cli, "_terminate_pids", lambda _pids: ([], [200]))
    monkeypatch.setattr(
        cli,
        "_release_motor_torque",
        lambda _config: (_ for _ in ()).throw(AssertionError("motor port must stay closed")),
    )
    monkeypatch.setattr(ipc, "cleanup_ipc_endpoint", lambda _path: False)

    cli._cmd_clear(args)

    output = capsys.readouterr().out
    assert "Failed to terminate PIDs: [200]" in output
    assert "Skipped torque release" in output


def test_cmd_clear_skip_kill_does_not_release_torque(monkeypatch, capsys):
    args = argparse.Namespace(config=None, skip_kill=True, skip_release=False)

    import lampgo.core.config as config_mod
    import lampgo.ipc as ipc

    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda config_path=None: SimpleNamespace(
            socket_path="tcp://127.0.0.1:28420",
            device=SimpleNamespace(motor_port="COM5", lamp_id="AL02"),
        ),
    )
    monkeypatch.setattr(
        cli,
        "_release_motor_torque",
        lambda _config: (_ for _ in ()).throw(AssertionError("motor port must stay closed")),
    )
    monkeypatch.setattr(ipc, "cleanup_ipc_endpoint", lambda _path: False)

    cli._cmd_clear(args)

    output = capsys.readouterr().out
    assert "Skip process cleanup" in output
    assert "torque release is also skipped" in output
    assert "Skipped torque release" in output


def test_cmd_clear_missing_psutil_does_not_release_torque(monkeypatch, capsys):
    args = argparse.Namespace(config=None, skip_kill=False, skip_release=False)

    import lampgo.core.config as config_mod
    import lampgo.ipc as ipc

    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda config_path=None: SimpleNamespace(
            socket_path="tcp://127.0.0.1:28420",
            device=SimpleNamespace(motor_port="COM5", lamp_id="AL02"),
        ),
    )
    monkeypatch.setattr(
        cli,
        "_release_motor_torque",
        lambda _config: (_ for _ in ()).throw(AssertionError("motor port must stay closed")),
    )
    monkeypatch.setattr(ipc, "cleanup_ipc_endpoint", lambda _path: False)

    cli._cmd_clear(args)

    captured = capsys.readouterr()
    assert "requires psutil" in captured.err
    assert "Skipped process cleanup" in captured.out
    assert "Skipped torque release" in captured.out


def test_resolve_calibration_port_prefers_cli_or_config(monkeypatch):
    args = argparse.Namespace(port=None)
    config = SimpleNamespace(device=SimpleNamespace(motor_port="/dev/tty.usbmodemA"))
    called = {"detect": False}

    def _detect_motor_port():
        called["detect"] = True
        return {"motor_port": "/dev/tty.usbmodemB", "messages": []}

    import lampgo.autodetect as autodetect

    monkeypatch.setattr(autodetect, "detect_motor_port", _detect_motor_port)
    port = cli._resolve_calibration_port(args, config)
    assert port == "/dev/tty.usbmodemA"
    assert called["detect"] is False


def test_resolve_calibration_port_falls_back_to_autodetect(monkeypatch):
    args = argparse.Namespace(port=None)
    config = SimpleNamespace(device=SimpleNamespace(motor_port=""))

    def _detect_motor_port():
        return {"motor_port": "/dev/tty.usbmodemB", "messages": ["Found 1 serial port(s)"]}

    import lampgo.autodetect as autodetect

    monkeypatch.setattr(autodetect, "detect_motor_port", _detect_motor_port)
    port = cli._resolve_calibration_port(args, config)
    assert port == "/dev/tty.usbmodemB"


def test_resolve_motor_port_auto_detect_ignores_saved_port(monkeypatch):
    args = argparse.Namespace(port=None, auto_detect=True)
    config = SimpleNamespace(device=SimpleNamespace(motor_port="COM7"))

    import lampgo.autodetect as autodetect

    monkeypatch.setattr(
        autodetect,
        "detect_motor_port",
        lambda: {
            "motor_port": "COM5",
            "motor_detection": "feetech_probe",
            "motor_candidates": ["COM5"],
            "messages": ["Motor bus detected: COM5"],
        },
    )

    assert cli._resolve_motor_port(args, config) == "COM5"


def test_resolve_motor_port_keeps_multiple_ports_ambiguous_when_noninteractive(
    monkeypatch,
    capsys,
):
    args = argparse.Namespace(port=None, auto_detect=True)
    config = SimpleNamespace(device=SimpleNamespace(motor_port="COM7"))

    import lampgo.autodetect as autodetect

    monkeypatch.setattr(
        autodetect,
        "detect_motor_port",
        lambda: {
            "motor_port": None,
            "motor_detection": "ambiguous",
            "motor_candidates": ["COM2", "COM5"],
            "messages": ["Candidate ports require confirmation"],
        },
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt="": (_ for _ in ()).throw(AssertionError("must not prompt")),
    )

    assert cli._resolve_motor_port(args, config, interactive=False) is None
    error = capsys.readouterr().err
    assert "COM2, COM5" in error
    assert "Use --port <COMx>" in error


def test_calibration_aborts_outside_project_root(monkeypatch, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'lampgo-test'\n", encoding="utf-8")
    subdirectory = tmp_path / "assets"
    subdirectory.mkdir()
    monkeypatch.chdir(subdirectory)

    with pytest.raises(SystemExit) as exc_info:
        cli._require_calibration_project_root()

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "project root" in error
    assert "before accessing hardware" in error


def test_calibration_accepts_project_root(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'lampgo-test'\n", encoding="utf-8")
    (tmp_path / "lampgo").mkdir()
    (tmp_path / "lampgo" / "cli.py").touch()
    monkeypatch.chdir(tmp_path)

    assert cli._require_calibration_project_root() == tmp_path.resolve()


def test_calibration_aborts_outside_a_lampgo_project(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        cli._require_calibration_project_root()

    assert exc_info.value.code == 2
    assert "project root" in capsys.readouterr().err


def test_calibration_aborts_when_target_is_outside_project(monkeypatch, tmp_path, capsys):
    project_root = tmp_path / "project"
    project_root.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        cli._require_calibration_path_in_project(project_root, tmp_path / "outside", "AL03")

    assert exc_info.value.code == 2
    assert "must stay inside" in capsys.readouterr().err


def test_load_config_from_args_degrades_to_no_hw_when_motor_port_missing(monkeypatch, capsys):
    """Missing motor_port must NOT exit; must degrade to no_hw so Web UI still boots."""
    args = argparse.Namespace(
        config=None,
        motor_port=None,
        led_port=None,
        lamp_id=None,
        recordings_dir=None,
    )

    fake_config = SimpleNamespace(
        device=SimpleNamespace(motor_port=""),
        no_hw=False,
        home_on_start=True,
    )

    import lampgo.autodetect as autodetect
    import lampgo.core.config as config_mod

    monkeypatch.setattr(config_mod, "load_config", lambda config_path=None, cli_overrides=None: fake_config)
    monkeypatch.setattr(autodetect, "detect_ports", lambda: {"motor_port": "", "messages": []})

    result = cli._load_config_from_args(args)
    assert result is fake_config
    assert result.no_hw is True
    assert result.home_on_start is False

    err = capsys.readouterr().err
    assert "no-hw" in err.lower()


def test_load_config_from_args_keeps_hw_when_motor_port_set(monkeypatch):
    """motor_port present → must leave no_hw alone."""
    args = argparse.Namespace(
        config=None,
        motor_port=None,
        led_port=None,
        lamp_id=None,
        recordings_dir=None,
    )

    fake_config = SimpleNamespace(
        device=SimpleNamespace(motor_port="/dev/ttyUSB0"),
        no_hw=False,
        home_on_start=True,
    )

    import lampgo.core.config as config_mod

    monkeypatch.setattr(config_mod, "load_config", lambda config_path=None, cli_overrides=None: fake_config)

    result = cli._load_config_from_args(args)
    assert result.no_hw is False
    assert result.home_on_start is True


def _wait_for(predicate, timeout: float = 1.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_virtual_motion_move_to_updates_joint_state():
    from lampgo.core.config import MotionConfig
    from lampgo.core.types import MotionTarget
    from lampgo.core.virtual_motion import VirtualMotionRuntime

    motion = VirtualMotionRuntime(MotionConfig(tick_rate_hz=80))
    motion.start()
    try:
        done = motion.move_to(MotionTarget(joints={"base_yaw": 18.0}, max_velocity=180.0))
        assert done.wait(timeout=1.0)
        assert abs(motion.current_state.get("base_yaw") - 18.0) < 0.5
        assert motion.status.is_done is True
    finally:
        motion.stop()


def test_virtual_motion_stream_frames_updates_joint_state():
    from lampgo.core.config import MotionConfig
    from lampgo.core.virtual_motion import VirtualMotionRuntime

    motion = VirtualMotionRuntime(MotionConfig(tick_rate_hz=80))
    motion.start()
    try:
        done = motion.stream_frames(
            [
                {"base_pitch": -5.0},
                {"base_pitch": -10.0},
                {"base_pitch": -15.0},
            ],
            fps=30,
        )
        assert done.wait(timeout=1.0)
        assert abs(motion.current_state.get("base_pitch") + 15.0) < 0.5
    finally:
        motion.stop()


def test_no_hw_server_uses_virtual_motion_for_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMPGO_IPC_TOKEN_FILE", str(tmp_path / "ipc-token"))

    async def run() -> None:
        from lampgo.core.config import LampgoConfig
        from lampgo.server import LampgoServer

        cfg = LampgoConfig(
            no_hw=True,
            home_on_start=False,
            socket_path=str(tmp_path / "lampgo.sock"),
        )
        server = LampgoServer(cfg)
        await server.start()
        try:
            assert getattr(server.motion, "is_virtual", False) is True
            assert server.motion.is_running is True

            result = await server.handle_request(
                {
                    "cmd": "invoke",
                    "skill_id": "move_to",
                    "params": {"base_yaw": 12.0, "velocity": 180.0},
                    "wait": True,
                }
            )

            assert result["ok"] is True
            assert result["result"]["status"] == "ok"
            assert _wait_for(lambda: abs(server.motion.current_state.get("base_yaw") - 12.0) < 0.5)
        finally:
            await server.shutdown()

    asyncio.run(run())


def test_server_status_exposes_hardware_startup_error() -> None:
    from lampgo.core.config import LampgoConfig
    from lampgo.server import LampgoServer

    server = LampgoServer(LampgoConfig(no_hw=True))
    server._hal_startup_error = "Unsafe startup pose; torque remains disabled"

    status = server._handle_status()["result"]

    assert status["device_health"] == "degraded"
    assert status["hardware_error"] == "Unsafe startup pose; torque remains disabled"


def test_server_status_exposes_recoverable_motor_startup_state() -> None:
    from lampgo.core.config import LampgoConfig
    from lampgo.core.hal import MotorStartupState
    from lampgo.server import LampgoServer

    server = LampgoServer(LampgoConfig(no_hw=True))
    server.hal._connected = True
    server.hal._startup_state = MotorStartupState.RECOVERY_REQUIRED
    server.hal._recovery_reason = "Recovery required: base_pitch is near a calibrated limit"

    status = server._handle_status()["result"]

    assert status["device_health"] == "degraded"
    assert status["hal_connected"] is True
    assert status["motor_startup_state"] == "recovery_required"
    assert status["hardware_error"].startswith("Recovery required")


def test_server_blocks_teach_recording_until_motor_recovery_finishes() -> None:
    async def run() -> None:
        from lampgo.core.config import LampgoConfig
        from lampgo.core.hal import MotorStartupState
        from lampgo.server import LampgoServer

        server = LampgoServer(LampgoConfig(no_hw=False))
        server.hal._connected = True
        server.hal._startup_state = MotorStartupState.RECOVERY_REQUIRED

        result = await server.start_recording_session()

        assert result == {
            "ok": False,
            "error": "motor recovery required; run return_safe before recording",
        }

    asyncio.run(run())


def test_cmd_ping_reports_status_error(monkeypatch, capsys):
    args = argparse.Namespace(port="/dev/tty.test", config=None)

    class FakePacketHandler:
        def ping(self, port_handler, motor_id):
            return 777, 0, 8

        def getTxRxResult(self, comm):
            return "COMM ERROR"

        def getRxPacketError(self, error):
            return "[RxPacketError] OverEle error!"

    class FakeBus:
        def __init__(self, port, motors):
            self.port = port
            self.motors = motors
            self.packet_handler = FakePacketHandler()
            self.port_handler = SimpleNamespace(closePort=lambda: None)

        def connect(self, handshake=False):
            return None

        def _is_comm_success(self, comm):
            return comm == 0

        def _is_error(self, error):
            return error != 0

    fake_motors_mod = SimpleNamespace(
        Motor=lambda id_, model, norm_mode: SimpleNamespace(id=id_, model=model),
        MotorNormMode=SimpleNamespace(DEGREES="degrees"),
    )
    fake_feetech_mod = SimpleNamespace(FeetechMotorsBus=FakeBus)
    monkeypatch.setitem(sys.modules, "lerobot.motors", fake_motors_mod)
    monkeypatch.setitem(sys.modules, "lerobot.motors.feetech", fake_feetech_mod)

    import lampgo.core.config as config_mod

    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda config_path=None: SimpleNamespace(
            device=SimpleNamespace(
                motor_port="/dev/tty.test",
                motors={"base_pitch": SimpleNamespace(id=2, model="sts3215")},
            )
        ),
    )

    try:
        cli._cmd_ping(args)
        raise AssertionError("Expected _cmd_ping to exit")
    except SystemExit as exc:
        assert exc.code == 1

    out = capsys.readouterr().out
    assert "STATUS ERROR" in out
    assert "OverEle error" in out


def test_cmd_setup_motors_assigns_each_configured_motor(monkeypatch, capsys):
    args = argparse.Namespace(port="/dev/tty.test", config=None)
    prompts: list[str] = []
    setup_calls: list[tuple[str, int]] = []

    class FakeBus:
        def __init__(self, port, motors):
            self.port = port
            self.motors = motors
            self.port_handler = SimpleNamespace(closePort=lambda: None)

        def connect(self, handshake=False):
            return None

        def setup_motor(self, motor_name):
            setup_calls.append((motor_name, self.motors[motor_name].id))

    fake_motors_mod = SimpleNamespace(
        Motor=lambda id_, model, norm_mode: SimpleNamespace(id=id_, model=model),
        MotorNormMode=SimpleNamespace(DEGREES="degrees"),
    )
    fake_feetech_mod = SimpleNamespace(FeetechMotorsBus=FakeBus)
    monkeypatch.setitem(sys.modules, "lerobot.motors", fake_motors_mod)
    monkeypatch.setitem(sys.modules, "lerobot.motors.feetech", fake_feetech_mod)
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")

    import lampgo.core.config as config_mod

    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda config_path=None: SimpleNamespace(
            device=SimpleNamespace(
                motor_port="/dev/tty.config",
                motors={
                    "base_yaw": SimpleNamespace(id=1, model="sts3215"),
                    "base_pitch": SimpleNamespace(id=2, model="sts3215"),
                },
            )
        ),
    )

    cli._cmd_setup_motors(args)

    assert setup_calls == [("base_yaw", 1), ("base_pitch", 2)]
    assert len(prompts) == 2
    assert "target ID 1" in prompts[0]
    assert "target ID 2" in prompts[1]
    out = capsys.readouterr().out
    assert "Connect exactly one motor" in out
    assert "All configured motor IDs" in out
