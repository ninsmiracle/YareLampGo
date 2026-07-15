"""CLI utility command tests."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from types import SimpleNamespace

from lampgo import cli


def test_build_help_text_contains_common_commands():
    text = cli._build_help_text()
    assert "uv run lampgo run" in text
    assert "uv run lampgo run --web" in text
    assert "http://localhost:8420" in text
    assert "uv run lampgo detect" in text
    assert "uv run lampgo scan-motors --ids 1-20" in text
    assert "uv run lampgo clear" in text
    assert "uv run lampgo setup-motors" in text
    assert "uv run lampgo calibrate" in text


def test_find_related_pids_filters_self_and_parent(monkeypatch):
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

    pids = cli._find_related_pids()
    assert pids == [200]


def test_resolve_calibration_port_prefers_cli_or_config(monkeypatch):
    args = argparse.Namespace(port=None)
    config = SimpleNamespace(device=SimpleNamespace(motor_port="/dev/tty.usbmodemA"))
    called = {"detect": False}

    def _detect_ports():
        called["detect"] = True
        return {"motor_port": "/dev/tty.usbmodemB", "messages": []}

    import lampgo.autodetect as autodetect

    monkeypatch.setattr(autodetect, "detect_ports", _detect_ports)
    port = cli._resolve_calibration_port(args, config)
    assert port == "/dev/tty.usbmodemA"
    assert called["detect"] is False


def test_resolve_calibration_port_falls_back_to_autodetect(monkeypatch):
    args = argparse.Namespace(port=None)
    config = SimpleNamespace(device=SimpleNamespace(motor_port=""))

    def _detect_ports():
        return {"motor_port": "/dev/tty.usbmodemB", "messages": ["Found 1 serial port(s)"]}

    import lampgo.autodetect as autodetect

    monkeypatch.setattr(autodetect, "detect_ports", _detect_ports)
    port = cli._resolve_calibration_port(args, config)
    assert port == "/dev/tty.usbmodemB"


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


def test_no_hw_server_uses_virtual_motion_for_skills(tmp_path):
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
