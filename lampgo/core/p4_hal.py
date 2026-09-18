"""Wireless motor HAL for the ESP32-P4 head-board executor.

The backend still generates trajectories in calibrated joint degrees.  This
adapter performs only transport and calibration-space conversion; the P4 owns
the UART bus, device-side limits, stale-command handling, and torque failsafes.
"""

from __future__ import annotations

import hashlib
import json
import math
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from lampgo.core.config import DeviceConfig
from lampgo.core.hal import MotorStartupState
from lampgo.core.types import DeviceHealth, JointState
from lampgo.device.p4_auth import build_p4_auth_fields

if TYPE_CHECKING:
    from lampgo.device.esp32 import Esp32DeviceManager

logger = structlog.get_logger(__name__)

PROTOCOL_VERSION = "lampgo-motion-v2"
ENCODER_MAX = 4095


class P4ControlError(RuntimeError):
    """A device rejection with its transaction outcome, distinct from a timeout."""

    def __init__(self, message: str, response: dict[str, Any]):
        super().__init__(message)
        self.response = dict(response)


@dataclass(frozen=True)
class _JointCalibration:
    name: str
    servo_id: int
    drive_mode: int
    homing_offset: int
    range_min: int
    range_max: int
    neutral_raw: int | None = None
    neutral_degrees: float | None = None


@dataclass
class _ControlRequest:
    op: str
    payload: dict[str, Any]
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    done: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None
    error: str | None = None
    cancelled: bool = False


class P4HardwareAbstraction:
    """Non-blocking WebSocket bridge from ``MotionRuntime`` to ESP32-P4.

    One worker owns the socket. ``write_positions`` replaces a single
    latest-value slot, so a slow network can never build an unsafe trajectory
    backlog in the 50 Hz motion thread.
    """

    def __init__(
        self,
        config: DeviceConfig,
        esp32_manager: Esp32DeviceManager,
        *,
        connection_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._esp32 = esp32_manager
        self._connection_factory = connection_factory
        self._calibration = self._load_calibration(config.calibration_dir / f"{config.lamp_id}.json")
        self._calibrated = bool(self._calibration)
        if not self._calibrated:
            self._calibration = {
                name: _JointCalibration(name, motor.id, 0, 0, 64, 4031, 2047, 0.0)
                for name, motor in config.motors.items()
            }
        self._by_id = {cal.servo_id: cal for cal in self._calibration.values()}

        self._state_lock = threading.Lock()
        self._latest_lock = threading.Lock()
        self._latest_frame: dict[str, Any] | None = None
        self._raw_positions: dict[int, int] = {}
        self._online_ids: set[int] = set()
        self._last_feedback_at = 0.0
        self._last_error: str | None = None
        self._connected = False
        self._torque_enabled = False
        self._startup_state = MotorStartupState.DISCONNECTED
        self._recovery_reason: str | None = None
        self._sequence = 0

        self._control_queue: queue.Queue[_ControlRequest] = queue.Queue(maxsize=16)
        self._pending_controls: dict[str, _ControlRequest] = {}
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._configure_on_connect = True
        self._maintenance = False
        self._device_snapshot: dict[str, Any] = {}
        self._recovery_raw_start: dict[str, int] | None = None
        self._recovery_raw_target: dict[str, int] | None = None
        self._last_heartbeat = 0.0

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self, calibrate: bool = True, *, configure: bool = True) -> None:
        if self._thread is not None:
            raise RuntimeError("P4 HAL already connected")
        if not self._calibration:
            action = "run local calibration first" if calibrate else "provide a calibration file"
            raise RuntimeError(f"P4 motion requires a complete calibration profile; {action}")
        if not bool(getattr(self._esp32, "enabled", False)):
            raise RuntimeError("P4 motor transport requires device_esp32.enabled=true")

        self._configure_on_connect = bool(configure)
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._worker, name="lampgo-p4-motion", daemon=True)
        self._thread.start()
        if self._ready.wait(timeout=float(self._config.p4_connect_timeout_s)):
            return

        error = self._last_error or "no P4 motion handshake received"
        self.disconnect()
        raise RuntimeError(f"P4 motion connection failed: {error}")

    def disconnect(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if self._connected and self._config.disable_torque_on_disconnect:
            try:
                self._submit_control("torque", {"enabled": False}, timeout_s=1.0)
            except Exception:
                logger.warning("p4_hal.torque_release_failed", exc_info=True)
        self._stop.set()
        thread.join(timeout=2.0)
        self._thread = None
        self._set_disconnected("transport stopped")
        self._startup_state = MotorStartupState.DISCONNECTED
        self._recovery_reason = None

    @property
    def is_connected(self) -> bool:
        with self._state_lock:
            return self._connected

    @property
    def startup_state(self) -> MotorStartupState:
        return self._startup_state

    @property
    def recovery_required(self) -> bool:
        return self._startup_state is MotorStartupState.RECOVERY_REQUIRED

    @property
    def recovery_reason(self) -> str | None:
        return self._recovery_reason

    @property
    def supports_remote_recovery(self) -> bool:
        return self._calibrated and not self._maintenance

    @property
    def motor_names(self) -> list[str]:
        return list(self._calibration)

    # ------------------------------------------------------------------
    # Motion-facing API
    # ------------------------------------------------------------------

    def read_positions(self) -> JointState:
        with self._state_lock:
            if not self._connected:
                raise RuntimeError(self._last_error or "P4 motion transport is disconnected")
            age = time.monotonic() - self._last_feedback_at
            if not self._raw_positions or age > float(self._config.p4_feedback_timeout_s):
                raise RuntimeError(f"P4 servo telemetry is stale ({age:.2f}s)")
            if self._online_ids != set(self._by_id):
                raise RuntimeError("P4 servo feedback incomplete; recording/motion stopped")
            positions = {
                cal.name: self._raw_to_degrees(self._raw_positions[servo_id], cal)
                for servo_id, cal in self._by_id.items()
                if servo_id in self._raw_positions
            }
        return JointState(positions=positions)

    def write_positions(self, positions: dict[str, float], move_time_ms: int = 0) -> None:
        if self._maintenance or not self._calibrated:
            raise RuntimeError("P4 is in maintenance or requires calibration")
        if not self.is_connected:
            raise RuntimeError(self._last_error or "P4 motion transport is disconnected")
        raw_targets: dict[str, int] = {}
        for name, value in positions.items():
            cal = self._calibration.get(name)
            if cal is None:
                raise ValueError(f"Unknown joint: {name}")
            raw = self._degrees_to_raw(float(value), cal)
            raw_targets[str(cal.servo_id)] = max(cal.range_min, min(cal.range_max, raw))

        with self._latest_lock:
            self._sequence += 1
            self._latest_frame = {
                "type": "frame",
                "seq": self._sequence,
                "move_time_ms": max(1, min(int(move_time_ms or 1), 1000)),
                "targets": raw_targets,
            }

    def write_recovery_positions(self, positions: dict[str, float]) -> None:
        if not self.is_connected or self.startup_state is not MotorStartupState.RECOVERING or self._maintenance:
            raise RuntimeError(
                "P4 recovery has not been prepared or is no longer active: "
                f"state={self.startup_state.value}, connected={self.is_connected}, "
                f"maintenance={self._maintenance}, "
                f"reason={self._recovery_reason or self._last_error or 'unspecified'}"
            )
        targets = self._recovery_targets(positions)
        with self._latest_lock:
            self._sequence += 1
            self._latest_frame = {
                "type": "frame",
                "seq": self._sequence,
                "move_time_ms": 20,
                "recovery": True,
                "targets": targets,
            }

    def read_recovery_start(self) -> dict[str, float]:
        if not self.recovery_required or not self.supports_remote_recovery:
            raise RuntimeError("P4 recovery is unavailable")
        response = self._submit_control("recovery_read", {})
        self._recovery_raw_start = self._require_raw_positions(response)
        return {
            name: self._raw_to_degrees(self._recovery_raw_start[str(cal.servo_id)], cal)
            for name, cal in self._calibration.items()
        }

    def prepare_recovery(self, frames: list[dict[str, float]]) -> dict[str, float]:
        if not self.recovery_required or not self.supports_remote_recovery or not self._recovery_raw_start:
            raise RuntimeError("Read a stable recovery start before preparing recovery")
        if not frames or len(frames) > 10000:
            raise ValueError("Invalid recovery frame count")
        start = self._recovery_raw_start
        target = self._recovery_targets(frames[-1])
        previous = dict(start)
        for name, cal in self._calibration.items():
            key = str(cal.servo_id)
            if not max(64, cal.range_min - 1024) <= start[key] <= min(4031, cal.range_max + 1024):
                raise ValueError(f"{name}: outside guarded recovery envelope")
            margin = max(12, (cal.range_max - cal.range_min) // 20)
            if not cal.range_min + margin <= target[key] <= cal.range_max - margin:
                raise ValueError(f"{name}: recovery target is too close to a limit")
            if abs(target[key] - int(cal.neutral_raw or 2047)) > 8:
                raise ValueError(f"{name}: recovery must end at calibrated neutral")
        for frame in frames:
            raw = self._recovery_targets(frame)
            for key, value in raw.items():
                delta = value - previous[key]
                if (
                    not min(start[key], target[key]) <= value <= max(start[key], target[key])
                    or delta * (target[key] - start[key]) < 0
                    or abs(delta) > 8
                ):
                    raise ValueError(f"ID {key}: recovery path reverses or exceeds step limit")
            previous = raw
        with self._latest_lock:
            self._latest_frame = None
        response = self._submit_control("recovery_prepare", {"starts": start, "targets": target}, timeout_s=5)
        self._apply_startup_response(response)
        if self.startup_state is not MotorStartupState.RECOVERING:
            raise RuntimeError("P4 did not enter verified recovery")
        self._recovery_raw_target = target
        raw = self._require_raw_positions(response)
        return {name: self._raw_to_degrees(raw[str(cal.servo_id)], cal) for name, cal in self._calibration.items()}

    def complete_recovery(self) -> None:
        with self._latest_lock:
            self._latest_frame = None
        response = self._submit_control("recovery_finish", {}, timeout_s=5)
        self._apply_startup_response(response)
        if self.startup_state is not MotorStartupState.READY:
            raise RuntimeError("P4 recovery did not finish")
        self._recovery_raw_start = self._recovery_raw_target = None

    def abort_recovery(self) -> None:
        if self.is_connected:
            self._submit_control("recovery_abort", {}, timeout_s=5)

    def _recovery_targets(self, positions: dict[str, float]) -> dict[str, int]:
        if set(positions) != set(self._calibration) or any(not math.isfinite(v) for v in positions.values()):
            raise ValueError("Recovery requires five finite joint positions")
        return {
            str(cal.servo_id): self._degrees_to_raw(positions[name], cal) for name, cal in self._calibration.items()
        }

    def _require_raw_positions(self, response: dict[str, Any]) -> dict[str, int]:
        raw = response.get("positions", {})
        if set(raw) != {str(i) for i in self._by_id} or set(response.get("online_ids", [])) != set(self._by_id):
            raise RuntimeError("P4 returned incomplete servo feedback")
        if any(type(v) is not int or not 0 <= v <= 4095 for v in raw.values()):
            raise RuntimeError("P4 returned invalid encoder positions")
        return dict(raw)

    def enter_maintenance(self) -> dict[str, Any]:
        with self._latest_lock:
            self._latest_frame = None
        response = self._submit_control("maintenance_enter", {}, timeout_s=5)
        if not response.get("maintenance") or response.get("torque_enabled", True):
            raise RuntimeError("P4 did not verify torque-off maintenance")
        self._maintenance = True
        return response

    def maintenance_snapshot(self) -> dict[str, Any]:
        if not self.is_connected or not self._maintenance:
            raise RuntimeError("Maintenance session disconnected; start again")
        response = self._submit_control("maintenance_read", {})
        self._require_raw_positions(response)
        if response.get("torque_enabled", True) or not response.get("maintenance"):
            raise RuntimeError("Maintenance no longer owns the servos")
        return response

    def exit_maintenance(self) -> dict[str, Any]:
        response = self._submit_control("maintenance_exit", {})
        self._maintenance = False
        return response

    def apply_calibration_file(self, pending: Path) -> None:
        calibration = self._load_calibration(pending)
        profile = self._profile_message(uuid.uuid4().hex, calibration=calibration, calibrated=True)
        response = self._submit_control("calibration_apply", {"profile": profile}, timeout_s=5)
        if response.get("profile_sha256") != profile["profile_sha256"]:
            raise RuntimeError("P4 calibration acknowledgement does not match; keep pending file for diagnosis")
        with self._state_lock:
            self._calibration = calibration
            self._by_id = {cal.servo_id: cal for cal in calibration.values()}
            self._calibrated = True

    def read_health(self) -> DeviceHealth:
        if not self.is_connected:
            return DeviceHealth.DISCONNECTED
        age = time.monotonic() - self._last_feedback_at
        expected = set(self._by_id)
        if age > float(self._config.p4_feedback_timeout_s) or self._online_ids != expected:
            return DeviceHealth.DEGRADED
        if self._startup_state not in {MotorStartupState.READY, MotorStartupState.RECOVERING} or self._maintenance:
            return DeviceHealth.DEGRADED
        return DeviceHealth.OK

    def disable_torque(self) -> None:
        if not self.is_connected:
            raise RuntimeError("P4 motion transport is disconnected")
        self._submit_control("torque", {"enabled": False})
        self._torque_enabled = False

    def enable_torque(self) -> None:
        if not self.is_connected:
            raise RuntimeError("P4 motion transport is disconnected")
        response = self._submit_control("torque", {"enabled": True})
        self._apply_startup_response(response)
        if self._startup_state is not MotorStartupState.READY:
            raise RuntimeError(self._recovery_reason or "P4 refused to enable motor torque")
        self._torque_enabled = True

    def get_calibration_home(self) -> dict[str, float] | None:
        if not self._calibrated:
            return None
        home: dict[str, float] = {}
        for cal in self._calibration.values():
            if cal.neutral_degrees is not None:
                home[cal.name] = round(cal.neutral_degrees, 1)
            elif cal.neutral_raw is not None:
                home[cal.name] = round(self._raw_to_degrees(cal.neutral_raw, cal), 1)
            else:
                home[cal.name] = round(self._raw_to_degrees(ENCODER_MAX / 2, cal), 1)
        return home or None

    def transport_status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "transport": "p4",
                "connected": self._connected,
                "torque_enabled": self._torque_enabled,
                "startup_state": self._startup_state.value,
                "recovery_reason": self._recovery_reason,
                "online_ids": sorted(self._online_ids),
                "telemetry_age_ms": (
                    round((time.monotonic() - self._last_feedback_at) * 1000) if self._last_feedback_at else None
                ),
                "error": self._last_error,
                "maintenance": self._maintenance,
                "calibrated": self._calibrated,
                "device": dict(self._device_snapshot),
            }

    # ------------------------------------------------------------------
    # Socket worker
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        retry_s = 0.2
        while not self._stop.is_set():
            host = self._esp32.get_active_host()
            if not host:
                self._last_error = "no ESP32-P4 host discovered; set device_esp32.preferred_host"
                self._stop.wait(retry_s)
                retry_s = min(1.0, retry_s * 1.5)
                continue
            try:
                self._serve(host)
                retry_s = 0.2
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                self._set_disconnected(str(exc))
                self._fail_pending(str(exc))
                with self._latest_lock:
                    self._latest_frame = None
                if not self._stop.is_set():
                    logger.warning("p4_hal.connection_lost", host=host, error=str(exc))
                    self._stop.wait(retry_s)
                    retry_s = min(2.0, retry_s * 1.5)

    def _serve(self, host: str) -> None:
        connection_factory = self._connection_factory
        if connection_factory is None:
            from websockets.sync.client import connect

            connection_factory = connect

        uri = f"ws://{host}:{self._config.p4_motion_port}/ws/motion"
        with connection_factory(
            uri,
            open_timeout=min(float(self._config.p4_connect_timeout_s), 5.0),
            close_timeout=1.0,
            max_size=256 * 1024,
            proxy=None,
        ) as socket:
            challenge = self._recv_challenge(socket, purpose="ws:motion", timeout_s=3.0)
            hello_id = uuid.uuid4().hex
            socket.send(
                json.dumps(
                    {
                        "type": "hello",
                        "protocol": PROTOCOL_VERSION,
                        "request_id": hello_id,
                        **build_p4_auth_fields(
                            owner_id=self._esp32.owner_id,
                            pairing_secret=self._esp32.pairing_secret,
                            purpose="ws:motion",
                            nonce=str(challenge["nonce"]),
                        ),
                    },
                    separators=(",", ":"),
                )
            )
            hello = self._recv_until(socket, hello_id, timeout_s=3.0)
            self._require_ok(hello, "hello")
            if hello.get("protocol") != PROTOCOL_VERSION:
                raise RuntimeError(f"unsupported P4 motion protocol: {hello.get('protocol')!r}")
            self._apply_telemetry(hello)

            profile_id = uuid.uuid4().hex
            socket.send(json.dumps(self._profile_message(profile_id), separators=(",", ":")))
            profile = self._recv_until(socket, profile_id, timeout_s=3.0)
            self._require_ok(profile, "profile")
            self._apply_telemetry(profile)
            self._apply_startup_response(profile)

            if self._configure_on_connect and self._calibrated and self._startup_state is MotorStartupState.READY:
                torque_id = uuid.uuid4().hex
                socket.send(
                    json.dumps(
                        {"type": "control", "op": "torque", "enabled": True, "request_id": torque_id},
                        separators=(",", ":"),
                    )
                )
                torque = self._recv_until(socket, torque_id, timeout_s=3.0)
                self._require_ok(torque, "torque")
                self._apply_startup_response(torque)
                self._torque_enabled = bool(torque.get("torque_enabled", True))

            with self._state_lock:
                self._connected = True
                self._last_error = None
            self._esp32.mark_active_healthy()
            self._ready.set()
            self._configure_on_connect = False  # Reconnect never silently energizes a released arm.
            logger.info("p4_hal.connected", host=host, startup_state=self._startup_state.value)

            while not self._stop.is_set():
                if time.monotonic() - self._last_heartbeat >= 1:
                    socket.send(json.dumps({"type": "control", "op": "heartbeat", "request_id": uuid.uuid4().hex}))
                    self._last_heartbeat = time.monotonic()
                self._send_queued(socket)
                try:
                    raw = socket.recv(timeout=0.01)
                except TimeoutError:
                    raw = None
                if raw is not None:
                    self._handle_message(self._decode_message(raw))
                if time.monotonic() - self._last_feedback_at > float(self._config.p4_feedback_timeout_s) * 2:
                    raise RuntimeError("P4 telemetry timeout")

    def _send_queued(self, socket: Any) -> None:
        try:
            control = self._control_queue.get_nowait()
        except queue.Empty:
            control = None
        if control is not None and not control.cancelled:
            message = {"type": "control", "op": control.op, "request_id": control.request_id, **control.payload}
            self._pending_controls[control.request_id] = control
            socket.send(json.dumps(message, separators=(",", ":")))

        with self._latest_lock:
            frame = self._latest_frame
            self._latest_frame = None
        if frame is not None:
            socket.send(json.dumps(frame, separators=(",", ":")))

    def _handle_message(self, message: dict[str, Any]) -> None:
        self._apply_telemetry(message)
        request_id = str(message.get("request_id") or "")
        control = self._pending_controls.pop(request_id, None)
        if control is not None:
            control.response = message
            if not bool(message.get("ok", False)):
                control.error = str(message.get("error") or f"P4 rejected {control.op}")
            control.done.set()

    def _recv_until(self, socket: Any, request_id: str, *, timeout_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                raw = socket.recv(timeout=remaining)
            except TimeoutError:
                continue
            message = self._decode_message(raw)
            self._apply_telemetry(message)
            if str(message.get("request_id") or "") == request_id:
                return message
        raise RuntimeError("P4 handshake timed out")

    def _recv_challenge(self, socket: Any, *, purpose: str, timeout_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                message = self._decode_message(socket.recv(timeout=remaining))
            except TimeoutError:
                continue
            if message.get("type") == "challenge" and message.get("purpose") == purpose and message.get("nonce"):
                return message
            self._apply_telemetry(message)
        raise RuntimeError("P4 motion authentication challenge timed out")

    @staticmethod
    def _decode_message(raw: str | bytes) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise RuntimeError("invalid P4 motion message")
        return message

    @staticmethod
    def _require_ok(message: dict[str, Any], operation: str) -> None:
        if not bool(message.get("ok", False)):
            raise RuntimeError(str(message.get("error") or f"P4 {operation} rejected"))

    def _submit_control(self, op: str, payload: dict[str, Any], *, timeout_s: float = 3.0) -> dict[str, Any]:
        request = _ControlRequest(op=op, payload=payload)
        try:
            self._control_queue.put(request, timeout=min(timeout_s, 0.5))
        except queue.Full as exc:
            raise RuntimeError("P4 control queue is full") from exc
        if not request.done.wait(timeout_s):
            request.cancelled = True
            raise RuntimeError(f"P4 {op} response timed out")
        if request.error:
            if request.response is not None:
                raise P4ControlError(request.error, request.response)
            raise RuntimeError(request.error)
        if not request.response or not request.response.get("ok"):
            raise RuntimeError(f"P4 {op} returned no successful acknowledgement")
        return request.response

    def _fail_pending(self, error: str) -> None:
        pending = list(self._pending_controls.values())
        self._pending_controls.clear()
        while True:
            try:
                pending.append(self._control_queue.get_nowait())
            except queue.Empty:
                break
        for request in pending:
            request.error = error
            request.done.set()

    def _set_disconnected(self, error: str) -> None:
        with self._state_lock:
            self._connected = False
            self._torque_enabled = False
            self._last_error = error
            self._maintenance = False
            self._online_ids.clear()

    # ------------------------------------------------------------------
    # Calibration and protocol helpers
    # ------------------------------------------------------------------

    def _profile_message(self, request_id: str, *, calibration=None, calibrated=None) -> dict[str, Any]:
        joints = [
            {
                "name": cal.name,
                "id": cal.servo_id,
                "range_min": cal.range_min,
                "range_max": cal.range_max,
                "homing_offset": cal.homing_offset,
                "neutral_raw": cal.neutral_raw if cal.neutral_raw is not None else 2047,
            }
            for cal in sorted((calibration or self._calibration).values(), key=lambda cal: cal.servo_id)
        ]
        canonical = json.dumps(joints, sort_keys=True, separators=(",", ":")).encode()
        return {
            "type": "profile",
            "request_id": request_id,
            "lamp_id": self._config.lamp_id,
            "profile_sha256": hashlib.sha256(canonical).hexdigest(),
            "max_torque_pct": int(self._config.max_torque_pct),
            "command_timeout_ms": 250,
            "release_timeout_ms": 2000,
            "joints": joints,
            "calibrated": self._calibrated if calibrated is None else calibrated,
        }

    def _apply_telemetry(self, message: dict[str, Any]) -> None:
        raw_positions = message.get("positions")
        online_ids = message.get("online_ids")
        has_servo_feedback = isinstance(raw_positions, dict) or isinstance(online_ids, list)
        has_runtime_state = "startup_state" in message or "torque_enabled" in message
        if not has_servo_feedback and not has_runtime_state:
            return
        with self._state_lock:
            previous_state = self._startup_state
            self._device_snapshot.update(
                {
                    k: v
                    for k, v in message.items()
                    if k
                    in {
                        "positions",
                        "online_ids",
                        "metrics",
                        "profile_sha256",
                        "startup_state",
                        "recovery_reason",
                        "maintenance_version",
                        "calibration_result",
                        "calibration_error",
                        "rollback_error",
                    }
                }
            )
            if "maintenance" in message:
                self._maintenance = bool(message["maintenance"])
            if isinstance(raw_positions, dict):
                parsed: dict[int, int] = {}
                for key, value in raw_positions.items():
                    try:
                        parsed[int(key)] = int(value)
                    except (TypeError, ValueError):
                        continue
                if parsed:
                    self._raw_positions.update(parsed)
            if isinstance(online_ids, list):
                parsed_ids: set[int] = set()
                for value in online_ids:
                    try:
                        parsed_ids.add(int(value))
                    except (TypeError, ValueError):
                        continue
                self._online_ids = parsed_ids
            if has_servo_feedback:
                self._last_feedback_at = time.monotonic()
            if "startup_state" in message:
                try:
                    self._startup_state = MotorStartupState(str(message.get("startup_state")))
                except ValueError:
                    self._startup_state = MotorStartupState.HARD_FAULT
                self._recovery_reason = str(message.get("recovery_reason") or "") or None
            if "torque_enabled" in message:
                self._torque_enabled = bool(message.get("torque_enabled"))
            if self._startup_state is not previous_state:
                logger.info(
                    "p4_hal.startup_state_changed",
                    old=previous_state.value,
                    new=self._startup_state.value,
                    reason=self._recovery_reason,
                    torque_enabled=self._torque_enabled,
                )

    def _apply_startup_response(self, response: dict[str, Any]) -> None:
        state = str(response.get("startup_state") or "hard_fault")
        with self._state_lock:
            try:
                self._startup_state = MotorStartupState(state)
            except ValueError:
                self._startup_state = MotorStartupState.HARD_FAULT
            self._recovery_reason = str(response.get("recovery_reason") or "") or None

    @staticmethod
    def _raw_to_degrees(raw: float, cal: _JointCalibration) -> float:
        midpoint = (cal.range_min + cal.range_max) / 2.0
        return (float(raw) - midpoint) * 360.0 / ENCODER_MAX

    @staticmethod
    def _degrees_to_raw(value: float, cal: _JointCalibration) -> int:
        midpoint = (cal.range_min + cal.range_max) / 2.0
        return round((value * ENCODER_MAX / 360.0) + midpoint)

    def _load_calibration(self, path: Path) -> dict[str, _JointCalibration]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Cannot load P4 calibration {path}: {exc}") from exc

        calibration: dict[str, _JointCalibration] = {}
        errors: list[str] = []
        for name, motor in self._config.motors.items():
            entry = data.get(name) if isinstance(data, dict) else None
            if not isinstance(entry, dict):
                errors.append(f"{name}: missing")
                continue
            try:
                cal = _JointCalibration(
                    name=name,
                    servo_id=int(entry["id"]),
                    drive_mode=int(entry["drive_mode"]),
                    homing_offset=int(entry["homing_offset"]),
                    range_min=int(entry["range_min"]),
                    range_max=int(entry["range_max"]),
                    neutral_raw=int(entry["neutral_raw"]) if entry.get("neutral_raw") is not None else None,
                    neutral_degrees=(
                        float(entry["neutral_degrees"]) if entry.get("neutral_degrees") is not None else None
                    ),
                )
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"{name}: invalid ({exc})")
                continue
            if cal.servo_id != int(motor.id):
                errors.append(f"{name}: expected id {motor.id}, got {cal.servo_id}")
            elif cal.drive_mode != 0 or abs(cal.homing_offset) > 2047:
                errors.append(f"{name}: unsupported drive mode or homing offset")
            elif not (0 <= cal.range_min < cal.range_max <= ENCODER_MAX):
                errors.append(f"{name}: invalid range {cal.range_min}..{cal.range_max}")
            else:
                calibration[name] = cal
        if errors:
            raise RuntimeError(f"Invalid P4 calibration {path}: {'; '.join(errors)}")
        return calibration
