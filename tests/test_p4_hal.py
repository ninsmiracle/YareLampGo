from __future__ import annotations

import json
import threading
import time
from collections import deque

import pytest

from lampgo.core.config import DeviceConfig
from lampgo.core.hal import MotorStartupState
from lampgo.core.p4_hal import P4HardwareAbstraction
from lampgo.core.types import DeviceHealth


class _FakeManager:
    enabled = True
    owner_id = "test-owner"
    pairing_secret = "test-secret"

    def __init__(self) -> None:
        self.healthy = False

    def get_active_host(self) -> str:
        return "192.0.2.4"

    def mark_active_healthy(self) -> None:
        self.healthy = True


class _FakeSocket:
    def __init__(self, *, startup_state: str = "ready") -> None:
        self.startup_state = startup_state
        self.sent: list[dict] = []
        self._responses: deque[str] = deque()
        self._condition = threading.Condition()
        self._queue({"type": "challenge", "purpose": "ws:motion", "nonce": "test-nonce"})

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def send(self, payload: str) -> None:
        message = json.loads(payload)
        with self._condition:
            self.sent.append(message)
            request_id = message.get("request_id")
            if message["type"] == "hello":
                self._queue(
                    {
                        "type": "hello",
                        "ok": True,
                        "request_id": request_id,
                        "protocol": "lampgo-motion-v1",
                        "positions": {str(i): 2048 for i in range(1, 6)},
                        "online_ids": list(range(1, 6)),
                    }
                )
            elif message["type"] == "profile":
                response = {
                    "type": "ack",
                    "ok": True,
                    "request_id": request_id,
                    "startup_state": self.startup_state,
                    "positions": {str(i): 2048 for i in range(1, 6)},
                    "online_ids": list(range(1, 6)),
                }
                if self.startup_state != "ready":
                    response["recovery_reason"] = "joint outside calibrated range"
                self._queue(response)
            elif message["type"] == "control":
                self._queue(
                    {
                        "type": "ack",
                        "ok": True,
                        "request_id": request_id,
                        "startup_state": self.startup_state,
                        "torque_enabled": bool(message.get("enabled")),
                        "positions": {str(i): 2048 for i in range(1, 6)},
                        "online_ids": list(range(1, 6)),
                    }
                )
            self._condition.notify_all()

    def recv(self, timeout: float | None = None) -> str:
        deadline = time.monotonic() + (timeout or 0)
        with self._condition:
            while not self._responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                self._condition.wait(remaining)
            return self._responses.popleft()

    def _queue(self, message: dict) -> None:
        self._responses.append(json.dumps(message))


def _config(tmp_path) -> DeviceConfig:
    calibration = {
        name: {
            "id": motor.id,
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 1000 + motor.id * 10,
            "range_max": 3000 + motor.id * 10,
            "neutral_raw": 2048,
            "neutral_degrees": 0.0,
        }
        for name, motor in DeviceConfig().motors.items()
    }
    (tmp_path / "TEST.json").write_text(json.dumps(calibration), encoding="utf-8")
    return DeviceConfig(
        motor_transport="p4",
        lamp_id="TEST",
        calibration_dir=tmp_path,
        p4_connect_timeout_s=1.0,
        p4_feedback_timeout_s=1.0,
    )


def test_p4_hal_handshake_profiles_then_enables_torque(tmp_path) -> None:
    socket = _FakeSocket()
    manager = _FakeManager()
    hal = P4HardwareAbstraction(_config(tmp_path), manager, connection_factory=lambda *_a, **_kw: socket)

    hal.connect()
    try:
        assert hal.is_connected is True
        assert hal.startup_state is MotorStartupState.READY
        assert hal.read_health() is DeviceHealth.OK
        assert manager.healthy is True
        assert [message["type"] for message in socket.sent[:3]] == ["hello", "profile", "control"]
        assert "pairing_secret" not in socket.sent[0]
        assert socket.sent[0]["auth_purpose"] == "ws:motion"
        assert socket.sent[0]["auth_nonce"] == "test-nonce"
        assert len(socket.sent[0]["auth_proof"]) == 64
        assert socket.sent[2]["op"] == "torque"
        assert socket.sent[2]["enabled"] is True

        state = hal.read_positions()
        assert set(state.positions) == set(_config(tmp_path).motors)

        hal.write_positions({"base_yaw": 0.0}, move_time_ms=20)
        deadline = time.monotonic() + 0.5
        while not any(message.get("type") == "frame" for message in socket.sent):
            assert time.monotonic() < deadline
            time.sleep(0.005)
        frame = next(message for message in socket.sent if message.get("type") == "frame")
        assert frame["targets"] == {"1": 2010}
        assert frame["move_time_ms"] == 20

        hal._handle_message(
            {
                "type": "telemetry",
                "ok": True,
                "startup_state": "hard_fault",
                "recovery_reason": "motion command timeout; torque released",
                "torque_enabled": False,
                "positions": {str(i): 2048 for i in range(1, 6)},
                "online_ids": list(range(1, 6)),
            }
        )
        assert hal.read_health() is DeviceHealth.DEGRADED
        assert hal.transport_status()["torque_enabled"] is False
    finally:
        hal.disconnect()


def test_p4_hal_keeps_torque_off_when_profile_needs_recovery(tmp_path) -> None:
    socket = _FakeSocket(startup_state="recovery_required")
    hal = P4HardwareAbstraction(
        _config(tmp_path),
        _FakeManager(),
        connection_factory=lambda *_a, **_kw: socket,
    )

    hal.connect()
    try:
        assert hal.recovery_required is True
        assert hal.supports_remote_recovery is False
        assert hal.read_health() is DeviceHealth.DEGRADED
        assert not any(message.get("type") == "control" and message.get("enabled") is True for message in socket.sent)
        with pytest.raises(RuntimeError, match="remote recovery is disabled"):
            hal.write_recovery_positions({"base_yaw": 0.0})
        with pytest.raises(RuntimeError, match="remote recovery is disabled"):
            hal.prepare_recovery([{"base_yaw": 0.0}])
    finally:
        hal.disconnect()


def test_p4_hal_requires_complete_calibration(tmp_path) -> None:
    (tmp_path / "TEST.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Invalid P4 calibration"):
        P4HardwareAbstraction(
            DeviceConfig(motor_transport="p4", lamp_id="TEST", calibration_dir=tmp_path),
            _FakeManager(),
        )


def test_default_transport_preserves_legacy_serial_hardware() -> None:
    """P4 is opt-in; existing S3/USB installations must not change route."""
    assert DeviceConfig().motor_transport == "serial"
