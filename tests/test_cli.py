"""CLI utility command tests."""

from __future__ import annotations

import argparse
import sys
from types import SimpleNamespace

from lampgo import cli


def test_build_help_text_contains_common_commands():
    text = cli._build_help_text()
    assert "uv run lampgo run" in text
    assert "uv run lampgo clear" in text
    assert "uv run lampgo calibrate" in text


def test_find_related_pids_filters_self_and_parent(monkeypatch):
    monkeypatch.setattr(cli.os, "getpid", lambda: 100)
    monkeypatch.setattr(cli.os, "getppid", lambda: 99)
    fake_ps = SimpleNamespace(
        stdout=(
            "100 uv run lampgo clear\n"
            "99 /bin/zsh -c uv run lampgo clear\n"
            "200 uv run lampgo run\n"
            "201 openclaw-gateway\n"
            "202 python something_else.py\n"
        )
    )
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: fake_ps)

    pids = cli._find_related_pids()
    assert pids == [200, 201]


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
