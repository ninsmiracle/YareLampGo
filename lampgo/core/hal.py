# SPDX-FileCopyrightText: 2026 @ninsmiracle, @shelly-tang, and LampGo contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Hardware Abstraction Layer for the LampGo Feetech motor bus.

The motor-bus lifecycle and calibration flow are inspired by LeLamp's
LeLampFollower runtime from humancomputerlab/LeLamp, which is licensed under
GPL-3.0. Low-level Feetech transport is provided by lerobot.
"""

from __future__ import annotations

import math
import time
from enum import StrEnum
from pprint import pformat
from typing import Any

import structlog

from lampgo.core.config import DeviceConfig
from lampgo.core.types import DeviceHealth, JointState

logger = structlog.get_logger(__name__)

try:
    from lerobot.motors import Motor, MotorCalibration, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode

    LEROBOT_AVAILABLE = True
except ImportError:
    LEROBOT_AVAILABLE = False
    logger.warning("lerobot not installed — HAL will use stub mode")


class MotorStartupState(StrEnum):
    """Torque/startup disposition for the physical motor bus."""

    DISCONNECTED = "disconnected"
    READY = "ready"
    RECOVERY_REQUIRED = "recovery_required"
    RECOVERING = "recovering"
    HARD_FAULT = "hard_fault"


class HardwareAbstraction:
    """Thin wrapper around the Feetech motor bus.

    Coordinates connect, calibration, configuration, and sync I/O for LampGo's
    five motor joints.
    """

    _HOME_EDGE_MARGIN_RATIO = 0.15
    _MIN_VALID_RANGE_COUNTS = 80
    _MAX_VALID_RANGE_COUNTS = 3500
    _MIN_NEUTRAL_MARGIN_RATIO = 0.10
    _STARTUP_EDGE_MARGIN_RATIO = 0.05
    _MIN_STARTUP_EDGE_MARGIN_COUNTS = 32
    _NEUTRAL_CONFIRM_TOLERANCE_COUNTS = 128
    _STS3215_MULTI_TURN_PHASE_BIT = 0x10
    _CALIBRATION_SCHEMA_VERSION = 2
    _RECOVERY_STABLE_SAMPLE_COUNT = 5
    _RECOVERY_STABLE_SAMPLE_INTERVAL_S = 0.02
    _RECOVERY_STABLE_SPREAD_COUNTS = 8
    _RECOVERY_MAX_FRAME_STEP_COUNTS = 8
    # Recovery must be strong enough to lift the arm under gravity.  These are
    # still well below the normal acceleration profile, but no longer limit an
    # STS3215 to the near-stall crawl used by the first recovery prototype.
    _RECOVERY_ACCELERATION_RAW = 5
    _RECOVERY_SPEED_RAW = 8
    _NORMAL_ACCELERATION_RAW = 50
    # Calibration limits describe the normal user-recorded operating range,
    # not every pose a torque-free linkage can reach under gravity. Permit a
    # one-way recovery from at most one quarter-turn beyond that range while
    # staying clear of the absolute encoder wrap boundary.
    _RECOVERY_MAX_OUTSIDE_RANGE_RATIO = 0.25
    _RECOVERY_ENCODER_WRAP_GUARD_RATIO = 1 / 64

    def __init__(self, config: DeviceConfig) -> None:
        self._config = config
        self._bus: Any | None = None
        self._connected = False
        self._status_error_motors: set[str] = set()
        self._startup_state = MotorStartupState.DISCONNECTED
        self._recovery_reason: str | None = None
        self._recovery_expanded_limit_motors: set[str] = set()
        self._recovery_profile_motors: set[str] = set()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self, calibrate: bool = True, *, configure: bool = True) -> None:
        if self._connected:
            raise RuntimeError("HAL already connected")

        if not LEROBOT_AVAILABLE:
            logger.info("hal.connect: stub mode (no lerobot)")
            self._connected = True
            self._startup_state = MotorStartupState.READY
            return

        norm_mode = MotorNormMode.DEGREES if self._config.use_degrees else MotorNormMode.RANGE_M100_100
        motors = {name: Motor(mc.id, mc.model, norm_mode) for name, mc in self._config.motors.items()}
        calibration = self._load_calibration()

        self._bus = FeetechMotorsBus(
            port=self._config.motor_port,
            motors=motors,
            calibration=calibration,
        )
        try:
            self._bus.connect(handshake=False)
            # A stale Goal_Position can become destructive as soon as torque is
            # enabled. Make torque-off the first register operation after the
            # serial port opens, before pinging or applying any calibration.
            self._release_torque_for_manual_motion(strict=True)
            self._verify_expected_motors()

            if calibration:
                logger.info("hal.calibration_loaded", lamp_id=self._config.lamp_id)
            elif calibrate:
                logger.info("Motor bus not calibrated — running interactive calibration")
                self.calibrate()

            if configure:
                self._startup_state = self._configure()
            else:
                self._startup_state = MotorStartupState.READY
        except Exception:
            self._startup_state = MotorStartupState.HARD_FAULT
            try:
                self._bus.disconnect(disable_torque=True)
            except Exception:
                logger.exception("hal.connect_cleanup_failed")
            self._bus = None
            raise
        self._connected = True
        logger.info(
            "hal.connected",
            port=self._config.motor_port,
            startup_state=self._startup_state.value,
        )

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self._bus is not None:
            try:
                if self._recovery_expanded_limit_motors or self._recovery_profile_motors:
                    self._release_torque_for_manual_motion(strict=False)
                    self._restore_normal_motion_profile(strict=False)
                    self._restore_calibrated_hardware_limits(strict=False)
                self._bus.disconnect(self._config.disable_torque_on_disconnect)
            except Exception:
                logger.exception("hal.disconnect_error")
                try:
                    self._bus.port_handler.closePort()
                except Exception:
                    logger.exception("hal.disconnect_force_close_error")
        self._connected = False
        self._status_error_motors.clear()
        self._bus = None
        self._startup_state = MotorStartupState.DISCONNECTED
        self._recovery_reason = None
        self._recovery_expanded_limit_motors.clear()
        self._recovery_profile_motors.clear()
        logger.info("hal.disconnected")

    @property
    def is_connected(self) -> bool:
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
    def motor_names(self) -> list[str]:
        if self._bus is not None:
            return list(self._bus.motors)
        return list(self._config.motors)

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    def read_positions(self) -> JointState:
        if not self._connected:
            raise RuntimeError("HAL not connected")
        if self._bus is None:
            return JointState(positions={name: 0.0 for name in self._config.motors})

        raw = self._bus.sync_read("Present_Position")
        return JointState(positions=dict(raw), timestamp=time.monotonic())

    def write_positions(self, positions: dict[str, float], move_time_ms: int = 0) -> None:
        if not self._connected:
            raise RuntimeError("HAL not connected")
        if self._bus is None:
            return

        if move_time_ms > 0:
            try:
                self._sync_write_position_time(positions, move_time_ms)
                return
            except Exception:
                logger.warning("hal.sync_write_position_time_failed", exc_info=True)

        self._bus.sync_write("Goal_Position", positions)

    def write_recovery_positions(self, positions: dict[str, float]) -> None:
        """Write one prevalidated recovery command with the guarded STS profile."""
        if not self._connected:
            raise RuntimeError("HAL not connected")
        if self._bus is None:
            return
        if self._startup_state is not MotorStartupState.RECOVERING:
            raise RuntimeError(f"Recovery write is unavailable in startup state '{self._startup_state.value}'.")
        # The recovery profile is configured and read back before torque-on.
        # Position-only writes preserve Goal_Time=0 and the explicit low
        # Goal_Velocity used by Feetech's STS position command protocol.
        self._bus.sync_write("Goal_Position", positions)

    # Set to True after the first successful Goal_Position+Goal_Time sync write.
    _goal_time_confirmed: bool = False

    def _sync_write_position_time(self, positions: dict[str, float], move_time_ms: int) -> None:
        """Write Goal_Position (addr 42, 2 B) + Goal_Time (addr 44, 2 B) in one sync-write packet.

        Goal_Time tells the STS3215 internal interpolator to reach the target within
        `move_time_ms` milliseconds, producing smooth motion instead of the default
        "full-speed dash then hard stop" behaviour.
        """
        # degrees → raw counts (handles calibration, drive_mode, DEGREES norm mode)
        raw_ids: dict[int, float] = self._bus._get_ids_values_dict(positions)
        raw_pos: dict[int, int] = self._bus._unnormalize(raw_ids)
        raw_pos = self._bus._encode_sign("Goal_Position", raw_pos)

        time_val = max(0, min(int(move_time_ms), 0xFFFF))
        time_bytes: list[int] = self._bus._serialize_data(time_val, 2)

        sw = self._bus.sync_writer
        sw.clearParam()
        sw.start_address = 42  # Goal_Position register address
        sw.data_length = 4  # 2 B position + 2 B time (consecutive registers)

        for id_, raw in raw_pos.items():
            pos_bytes: list[int] = self._bus._serialize_data(raw, 2)
            sw.addParam(id_, pos_bytes + time_bytes)

        comm = sw.txPacket()

        if not HardwareAbstraction._goal_time_confirmed:
            success = self._bus._is_comm_success(comm)
            HardwareAbstraction._goal_time_confirmed = True
            if success:
                logger.info(
                    "hal.goal_time_active",
                    move_time_ms=time_val,
                    motors=list(positions.keys()),
                )
            else:
                logger.warning(
                    "hal.goal_time_failed",
                    comm_result=self._bus.packet_handler.getTxRxResult(comm),
                    move_time_ms=time_val,
                )

    def read_health(self) -> DeviceHealth:
        if not self._connected:
            return DeviceHealth.DISCONNECTED
        if self._bus is None:
            return DeviceHealth.DEGRADED
        if self._startup_state is not MotorStartupState.READY:
            return DeviceHealth.DEGRADED
        return DeviceHealth.OK

    def disable_torque(self) -> None:
        """Disable motor torque so joints can be moved by hand.

        This is primarily used by teach-recording workflows (`lampgo record`).
        In stub mode this is a no-op.
        """
        if not self._connected:
            raise RuntimeError("HAL not connected")
        if self._bus is None:
            logger.info("hal.disable_torque: stub mode")
            return
        self._release_torque_for_manual_motion(strict=False)
        logger.info("hal.torque_disabled")

    def enable_torque(self) -> None:
        """Enable motor torque so joints hold current pose."""
        if not self._connected:
            raise RuntimeError("HAL not connected")
        if self._bus is None:
            logger.info("hal.enable_torque: stub mode")
            return
        # Re-run the complete fail-closed startup sequence. Recording can leave
        # an arm at a range edge, and blindly restoring torque there is unsafe.
        try:
            state = self._configure()
        except Exception:
            self._release_torque_for_manual_motion(strict=False)
            raise
        if state is MotorStartupState.RECOVERY_REQUIRED:
            self._startup_state = state
            raise RuntimeError(self._recovery_reason or "Motor recovery is required before enabling torque.")
        self._startup_state = state
        logger.info("hal.torque_enabled")

    def get_calibration_home(self) -> dict[str, float] | None:
        """Return the user-confirmed neutral pose in degree-mode coordinates.

        New calibration files store the neutral pose explicitly. Older files are
        migrated in-memory by falling back to the historical half-turn neutral.
        """
        data = self._load_calibration_data()
        calibration = self._calibration_from_data(data)
        if not calibration:
            return None

        home: dict[str, float] = {}
        for name, cal in calibration.items():
            entry = data.get(name, {}) if isinstance(data, dict) else {}
            if isinstance(entry, dict) and "neutral_degrees" in entry:
                home[name] = round(float(entry["neutral_degrees"]), 1)
                continue
            if isinstance(entry, dict) and "neutral_raw" in entry:
                home[name] = self._raw_to_degrees(float(entry["neutral_raw"]), cal)
                continue
            home[name] = self._fallback_half_turn_home(name, cal)
        logger.info("hal.calibration_home", home=home)
        return home

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def calibrate(self) -> None:
        if self._bus is None:
            raise RuntimeError("Cannot calibrate in stub mode")

        logger.info("calibration.preflight")
        self._release_torque_for_manual_motion(strict=True)
        changed_mode = self._ensure_sts3215_single_turn(list(self._bus.motors))

        existing = self._load_calibration()
        if existing:
            choice = input(
                f"Press ENTER to use existing calibration for '{self._config.lamp_id}', or type 'c' to re-calibrate: "
            )
            if choice.strip().lower() != "c":
                if changed_mode:
                    changed = ", ".join(sorted(changed_mode))
                    raise RuntimeError(
                        "STS3215 single-turn mode was repaired for "
                        f"{changed}. The existing calibration cannot be reused; "
                        "run calibration again and choose 'c'."
                    )
                self._require_single_turn_calibration_metadata(list(self._bus.motors))
                self._apply_hardware_calibration(existing)
                logger.info("calibration.loaded", lamp_id=self._config.lamp_id)
                return

        logger.info("calibration.start")
        for motor in self._bus.motors:
            self._write_register_or_raise("Operating_Mode", motor, OperatingMode.POSITION.value)
        self._open_manual_calibration_range()

        input("Move arm to the middle of its range of motion and press ENTER...")
        homing_offsets = self._bus.set_half_turn_homings()

        for motor_name, expected in homing_offsets.items():
            actual = self._bus.read("Homing_Offset", motor_name, normalize=False)
            if actual != expected:
                logger.warning(
                    "calibration.homing_offset_mismatch",
                    motor=motor_name,
                    expected=expected,
                    actual=actual,
                )
                self._bus.write("Homing_Offset", motor_name, expected, num_retry=3)
                verify = self._bus.read("Homing_Offset", motor_name, normalize=False)
                if verify != expected:
                    raise RuntimeError(
                        f"Cannot write Homing_Offset for '{motor_name}': "
                        f"expected {expected}, got {verify}. Check motor connection."
                    )

        neutral_raw = self._bus.sync_read(
            "Present_Position",
            list(self._bus.motors),
            normalize=False,
        )
        logger.info(
            "calibration.neutral_recorded",
            neutral_raw={k: int(v) for k, v in neutral_raw.items()},
        )

        print("Move all joints through their full ranges of motion.\nPress ENTER to stop recording...")
        range_mins, range_maxes = self._bus.record_ranges_of_motion()
        self._validate_recorded_calibration(neutral_raw, range_mins, range_maxes)

        calibration: dict[str, MotorCalibration] = {}
        for motor_name, m in self._bus.motors.items():
            calibration[motor_name] = MotorCalibration(
                id=m.id,
                drive_mode=0,
                homing_offset=homing_offsets[motor_name],
                range_min=range_mins[motor_name],
                range_max=range_maxes[motor_name],
            )

        self._apply_hardware_calibration(calibration)
        self._wait_for_safe_neutral_pose(neutral_raw, calibration)
        self._save_calibration(calibration, neutral_raw=neutral_raw)
        logger.info("calibration.saved", lamp_id=self._config.lamp_id)

    def setup_motors(self) -> None:
        if self._bus is None:
            raise RuntimeError("Cannot setup motors in stub mode")
        for motor in reversed(list(self._bus.motors)):
            input(f"Connect only the '{motor}' motor and press ENTER.")
            self._bus.setup_motor(motor)
            print(f"  '{motor}' ID set to {self._bus.motors[motor].id}")

    # ------------------------------------------------------------------
    # Internal motor-bus configuration
    # ------------------------------------------------------------------

    def _verify_expected_motors(self) -> None:
        if self._bus is None:
            return

        expected_models = {m.id: self._bus.model_number_table[m.model] for m in self._bus.motors.values()}
        found_models: dict[int, int] = {}
        wrong_models: dict[int, tuple[int, int]] = {}
        status_errors: dict[str, str] = {}

        for motor_name, motor in self._bus.motors.items():
            model_number, comm, error = self._bus.packet_handler.ping(self._bus.port_handler, motor.id)
            if not self._bus._is_comm_success(comm):
                continue

            found_models[motor.id] = model_number
            expected_model = expected_models[motor.id]
            if model_number != expected_model:
                wrong_models[motor.id] = (expected_model, model_number)
            if self._bus._is_error(error):
                status_errors[motor_name] = self._bus.packet_handler.getRxPacketError(error)

        missing_ids = [id_ for id_ in self._bus.ids if id_ not in found_models]
        self._status_error_motors = set(status_errors)
        if status_errors:
            logger.warning("hal.motor_ping_status_errors", errors=status_errors)

        if not missing_ids and not wrong_models:
            return

        error_lines = [f"FeetechMotorsBus motor check failed on port '{self._config.motor_port}':"]
        if missing_ids:
            error_lines.append("\nMissing motor IDs:")
            error_lines.extend(f"  - {id_} (expected model: {expected_models[id_]})" for id_ in missing_ids)
        if wrong_models:
            error_lines.append("\nMotors with incorrect model numbers:")
            error_lines.extend(
                f"  - {id_} ({self._bus._id_to_name(id_)}): expected {expected}, found {found}"
                for id_, (expected, found) in wrong_models.items()
            )
        error_lines.append("\nFull expected motor list (id: model_number):")
        error_lines.append(pformat(expected_models, indent=4, sort_dicts=False))
        error_lines.append("\nFull found motor list (id: model_number):")
        error_lines.append(pformat(found_models, indent=4, sort_dicts=False))
        raise RuntimeError("\n".join(error_lines))

    def _configure(self) -> MotorStartupState:
        if self._bus is None:
            return MotorStartupState.READY

        healthy_motors = [m for m in self._bus.motors if m not in self._status_error_motors]
        if len(healthy_motors) != len(self._bus.motors):
            logger.warning(
                "hal.configure_skipping_status_error_motors",
                skipped=sorted(self._status_error_motors),
            )
        if not healthy_motors:
            logger.warning("hal.configure_skipped_all_motors")
            return MotorStartupState.READY

        self._release_torque_for_manual_motion(strict=True)

        changed_mode = self._ensure_sts3215_single_turn(healthy_motors)
        if changed_mode:
            changed = ", ".join(sorted(changed_mode))
            raise RuntimeError(
                "STS3215 single-turn mode was repaired for "
                f"{changed}. Existing calibration is no longer trusted; "
                "run `uv run lampgo calibrate` before enabling torque."
            )
        self._require_single_turn_calibration_metadata(healthy_motors)
        self._ensure_hardware_calibration()

        # Torque_Limit (0-1000) caps stall current for every motor.
        # Keeps the bus servo adapter board from overheating when servos are
        # stalled against mechanical stops. 800 = 80 % of rated torque by default.
        torque_limit_raw = max(100, min(1000, int(self._config.max_torque_pct * 10)))

        for motor in healthy_motors:
            # Configure each motor independently so one flaky status packet
            # does not block the whole arm from starting up.
            self._safe_bus_write("Return_Delay_Time", motor, 0)
            if getattr(self._bus, "protocol_version", None) == 0:
                self._safe_bus_write("Maximum_Acceleration", motor, self._NORMAL_ACCELERATION_RAW)
            self._safe_bus_write("Acceleration", motor, self._NORMAL_ACCELERATION_RAW)
            self._write_register_or_raise("Operating_Mode", motor, OperatingMode.POSITION.value)
            self._safe_bus_write("P_Coefficient", motor, 16)
            self._safe_bus_write("I_Coefficient", motor, 0)
            self._safe_bus_write("D_Coefficient", motor, 32)
            self._write_register_or_raise("Torque_Limit", motor, torque_limit_raw, normalize=False)
            # Clear stale SRAM motion fields left by an interrupted process
            # before any goal is seeded or torque can be enabled.
            self._write_register_or_raise("Goal_Time", motor, 0, normalize=False)
            self._write_register_or_raise("Goal_Velocity", motor, 0, normalize=False)
        logger.info("hal.torque_limit_set", pct=self._config.max_torque_pct, raw=torque_limit_raw)

        recovery_required = self._seed_goal_positions_to_present(
            healthy_motors,
            allow_recovery=True,
        )
        if recovery_required:
            logger.warning(
                "hal.recovery_required",
                reason=self._recovery_reason,
            )
            return MotorStartupState.RECOVERY_REQUIRED

        for motor in healthy_motors:
            self._write_register_or_raise("Lock", motor, 1)
            self._write_register_or_raise("Torque_Enable", motor, 1)
        self._recovery_reason = None
        return MotorStartupState.READY

    def _safe_bus_write(
        self,
        data_name: str,
        motor: str,
        value: Any,
        *,
        normalize: bool = True,
        num_retry: int = 3,
    ) -> bool:
        if self._bus is None:
            return False
        try:
            self._bus.write(data_name, motor, value, normalize=normalize, num_retry=num_retry)
            return True
        except Exception:
            logger.warning(
                "hal.configure_write_failed",
                motor=motor,
                register=data_name,
                value=value,
                normalize=normalize,
                num_retry=num_retry,
                exc_info=True,
            )
            return False

    def _write_register_or_raise(
        self,
        data_name: str,
        motor: str,
        value: Any,
        *,
        normalize: bool = True,
        num_retry: int = 3,
    ) -> None:
        if not self._safe_bus_write(
            data_name,
            motor,
            value,
            normalize=normalize,
            num_retry=num_retry,
        ):
            raise RuntimeError(f"Cannot write {data_name}={value!r} for '{motor}'.")

        try:
            actual = self._bus.read(data_name, motor, normalize=normalize)
        except Exception as exc:
            raise RuntimeError(f"Cannot read back {data_name} for '{motor}' after writing {value!r}.") from exc

        if int(actual) != int(value):
            raise RuntimeError(f"Cannot verify {data_name} for '{motor}': expected {value}, got {actual}.")

    def _ensure_sts3215_single_turn(self, motors: list[str]) -> set[str]:
        """Force STS3215 angle feedback into single-turn mode and verify it."""
        if self._bus is None:
            return set()

        changed: set[str] = set()
        for motor in motors:
            motor_config = self._bus.motors[motor]
            if getattr(motor_config, "model", "") != "sts3215":
                continue

            phase = int(self._bus.read("Phase", motor, normalize=False))
            if phase & self._STS3215_MULTI_TURN_PHASE_BIT:
                single_turn_phase = phase & ~self._STS3215_MULTI_TURN_PHASE_BIT
                self._write_register_or_raise("Phase", motor, single_turn_phase, normalize=False)
                changed.add(motor)
                phase = int(self._bus.read("Phase", motor, normalize=False))

            if phase & self._STS3215_MULTI_TURN_PHASE_BIT:
                raise RuntimeError(
                    f"Motor '{motor}' is still in unsafe STS3215 multi-turn feedback mode. Torque will remain disabled."
                )

        logger.info("hal.sts3215_single_turn_verified", motors=sorted(motors), changed=sorted(changed))
        return changed

    def _require_single_turn_calibration_metadata(self, motors: list[str]) -> None:
        """Reject calibration captured before single-turn mode was verified."""
        if self._bus is None or not self._bus.calibration:
            return
        if not any(getattr(self._bus.motors[motor], "model", "") == "sts3215" for motor in motors):
            return

        data = self._load_calibration_data() or {}
        metadata = data.get("_meta", {}) if isinstance(data, dict) else {}
        if not isinstance(metadata, dict) or metadata.get("sts3215_single_turn_verified") is not True:
            raise RuntimeError(
                "Calibration predates STS3215 single-turn safety verification. "
                "Torque will remain disabled; run `uv run lampgo calibrate` to create a safe profile."
            )

    def _validate_recorded_calibration(
        self,
        neutral_raw: dict[str, float],
        range_mins: dict[str, float],
        range_maxes: dict[str, float],
    ) -> None:
        """Reject overflowed or severely off-centre ranges before hardware limits are written."""
        if self._bus is None:
            raise RuntimeError("Cannot validate calibration without a motor bus")

        errors: list[str] = []
        for motor, motor_config in self._bus.motors.items():
            resolution_max = int(self._bus.model_resolution_table[motor_config.model]) - 1
            neutral = int(neutral_raw[motor])
            range_min = int(range_mins[motor])
            range_max = int(range_maxes[motor])
            span = range_max - range_min
            margin = min(neutral - range_min, range_max - neutral)

            if not (0 <= range_min < range_max <= resolution_max):
                errors.append(f"{motor}: range {range_min}..{range_max} is outside single-turn 0..{resolution_max}")
                continue
            if span < self._MIN_VALID_RANGE_COUNTS or span > self._MAX_VALID_RANGE_COUNTS:
                errors.append(f"{motor}: unsafe range span {span}")
                continue
            if margin < span * self._MIN_NEUTRAL_MARGIN_RATIO:
                errors.append(f"{motor}: neutral {neutral} is too close to range edge {range_min}..{range_max}")

        if errors:
            raise RuntimeError(
                "Unsafe calibration rejected before motor limits were written:\n- " + "\n- ".join(errors)
            )

    def _validate_neutral_return(
        self,
        neutral_raw: dict[str, float],
        final_raw: dict[str, float],
    ) -> None:
        """Require the arm to be returned near its recorded neutral before saving."""
        errors: list[str] = []
        for motor, neutral in neutral_raw.items():
            if motor not in final_raw:
                errors.append(f"{motor}: final position could not be read")
                continue
            final = int(final_raw[motor])
            if abs(final - int(neutral)) > self._NEUTRAL_CONFIRM_TOLERANCE_COUNTS:
                errors.append(f"{motor}: expected near {int(neutral)}, got {final}")
        if errors:
            raise RuntimeError("Arm is not close to its recorded neutral pose:\n- " + "\n- ".join(errors))

    def _wait_for_safe_neutral_pose(
        self,
        neutral_raw: dict[str, float],
        calibration: dict[str, Any],
    ) -> dict[str, float]:
        """Keep torque off and let the user correct the final pose without recalibrating."""
        if self._bus is None:
            raise RuntimeError("Cannot confirm neutral pose without a motor bus")

        while True:
            input(
                "Move every joint to a safe supported pose, preferably the recorded neutral pose; "
                "do not leave a joint at its range limit. Press ENTER to verify..."
            )
            final_raw = self._bus.sync_read(
                "Present_Position",
                list(self._bus.motors),
                normalize=False,
            )
            try:
                self._validate_startup_positions(final_raw, calibration=calibration)
            except RuntimeError as exc:
                print(
                    f"{exc}\nCalibration has not been saved yet. "
                    "Torque remains disabled; reposition the reported joint and try again."
                )
                continue
            try:
                self._validate_neutral_return(neutral_raw, final_raw)
            except RuntimeError as exc:
                # Exact neutral is a convenience for the subsequent return_safe
                # motion, not a prerequisite for safely enabling torque. A pose
                # well inside every calibrated limit is safe to save and hold.
                logger.warning(
                    "calibration.final_pose_away_from_neutral",
                    detail=str(exc),
                    final_raw={motor: int(raw) for motor, raw in final_raw.items()},
                )
                print(f"Warning: {exc}\nThe pose is safely inside all calibrated limits, so calibration will be saved.")
            return final_raw

    def _release_torque_for_manual_motion(self, *, strict: bool) -> None:
        """Release every motor before hand-guided calibration/recording.

        Feetech configuration writes are slow and occasionally flaky on a busy
        serial bus. Use explicit per-register retries and then verify the two
        registers that matter for safe hand movement.
        """
        if self._bus is None:
            return

        motors = list(self._bus.motors)
        for motor in motors:
            self._safe_bus_write("Torque_Enable", motor, 0, num_retry=3)
            self._safe_bus_write("Lock", motor, 0, num_retry=3)

        try:
            self._bus.disable_torque(motors, num_retry=3)
        except TypeError:
            try:
                self._bus.disable_torque()
            except Exception:
                logger.warning("hal.disable_torque_bus_call_failed", exc_info=True)
        except Exception:
            logger.warning("hal.disable_torque_bus_call_failed", exc_info=True)

        bad: dict[str, dict[str, Any]] = {}
        for motor in motors:
            state: dict[str, Any] = {}
            for register in ("Torque_Enable", "Lock"):
                try:
                    state[register] = self._bus.read(register, motor, normalize=False)
                except Exception as exc:
                    state[register] = f"read_failed:{type(exc).__name__}"
            if state.get("Torque_Enable") != 0 or state.get("Lock") != 0:
                bad[motor] = state

        if bad:
            logger.warning("hal.torque_release_verify_failed", motors=bad)
            if strict:
                detail = ", ".join(f"{motor}={state}" for motor, state in bad.items())
                raise RuntimeError(f"Motor torque/lock registers did not release before manual motion: {detail}")

    def _open_manual_calibration_range(self) -> None:
        """Clear stale hardware limits before asking the user to move joints by hand."""
        if self._bus is None:
            return

        opened: dict[str, dict[str, int]] = {}
        for motor, config in self._bus.motors.items():
            model = config.model
            max_position = int(self._bus.model_resolution_table[model]) - 1
            self._write_register_or_raise("Homing_Offset", motor, 0, normalize=False)
            self._write_register_or_raise("Min_Position_Limit", motor, 0, normalize=False)
            self._write_register_or_raise(
                "Max_Position_Limit",
                motor,
                max_position,
                normalize=False,
            )
            opened[motor] = {
                "min": 0,
                "max": max_position,
            }

        logger.info("calibration.manual_range_opened", motors=opened)

    def _ensure_hardware_calibration(self) -> None:
        if self._bus is None or not self._bus.calibration:
            return
        if self._bus.is_calibrated:
            logger.info("hal.calibration_already_active", lamp_id=self._config.lamp_id)
            return
        self._apply_hardware_calibration(self._bus.calibration)

    def _apply_hardware_calibration(self, calibration: dict[str, Any]) -> None:
        """Write calibration and refuse torque unless every register reads back."""
        if self._bus is None:
            raise RuntimeError("Cannot apply calibration without a motor bus")
        self._bus.write_calibration(calibration)
        if not self._bus.is_calibrated:
            raise RuntimeError("Motor calibration register verification failed. Torque will remain disabled.")
        logger.info("hal.calibration_applied", lamp_id=self._config.lamp_id)

    def _seed_goal_positions_to_present(
        self,
        motors: list[str],
        *,
        allow_recovery: bool = False,
    ) -> bool:
        if self._bus is None or not motors:
            return False

        try:
            present_raw = self._bus.sync_read("Present_Position", motors, normalize=False)
        except Exception:
            logger.warning("hal.goal_seed_sync_read_failed", motors=motors, exc_info=True)
            present_raw = {}
            for motor in motors:
                try:
                    present_raw[motor] = self._bus.read("Present_Position", motor, normalize=False)
                except Exception:
                    logger.warning("hal.goal_seed_read_failed", motor=motor, exc_info=True)

        missing = [motor for motor in motors if motor not in present_raw]
        if missing:
            raise RuntimeError(f"Cannot safely seed Goal_Position; missing Present_Position for {missing}.")

        for motor, raw in present_raw.items():
            motor_config = self._bus.motors[motor]
            resolution_max = int(self._bus.model_resolution_table[motor_config.model]) - 1
            if not 0 <= int(raw) <= resolution_max:
                raise RuntimeError(
                    f"Unsafe Present_Position for '{motor}': {int(raw)} is outside 0..{resolution_max}. "
                    "Torque will remain disabled."
                )

        recovery_required = self._validate_startup_positions(
            present_raw,
            allow_recovery=allow_recovery,
        )
        if recovery_required:
            # Do not even seed a goal in the recoverable startup state. The
            # current position and the complete return-safe path are re-read and
            # validated immediately before the explicit recovery action.
            return True

        seeded: dict[str, int] = {}
        for motor, raw in present_raw.items():
            raw_int = int(raw)
            self._write_register_or_raise("Goal_Position", motor, raw_int, normalize=False)
            seeded[motor] = raw_int

        if seeded:
            logger.info("hal.goal_seeded_to_present", present_raw=seeded)
        return False

    def _validate_startup_positions(
        self,
        present_raw: dict[str, float],
        *,
        calibration: dict[str, Any] | None = None,
        allow_recovery: bool = False,
    ) -> bool:
        """Classify startup feedback without confusing calibration with trust.

        Calibration limits remain the hard boundary for normal motion. A stable
        torque-free pose may sit a bounded distance outside those limits under
        gravity, however, so ``allow_recovery`` also accepts a guarded subset of
        the verified single-turn encoder range. Such a pose never enables torque
        during startup; only a prevalidated one-way ``return_safe`` may do so.
        """
        if self._bus is None:
            raise RuntimeError("Cannot validate startup positions without a motor bus")
        active_calibration = calibration if calibration is not None else self._bus.calibration
        if not active_calibration:
            return False

        hard_errors: list[str] = []
        recoverable_errors: list[str] = []
        for motor, raw in present_raw.items():
            cal = active_calibration.get(motor)
            if cal is None:
                hard_errors.append(f"{motor}: calibration is missing")
                continue

            raw_int = int(raw)
            range_min = int(cal.range_min)
            range_max = int(cal.range_max)
            span = range_max - range_min
            margin = min(raw_int - range_min, range_max - raw_int)
            required_margin = max(
                self._MIN_STARTUP_EDGE_MARGIN_COUNTS,
                int(span * self._STARTUP_EDGE_MARGIN_RATIO),
            )
            recovery_min, recovery_max = self._recovery_envelope_bounds(motor, cal)
            if raw_int < range_min or raw_int > range_max:
                outside_detail = f"{motor}: position {raw_int} is outside calibrated range {range_min}..{range_max}"
                if allow_recovery and recovery_min <= raw_int <= recovery_max:
                    recoverable_errors.append(
                        f"{outside_detail}, but remains inside guarded single-turn recovery "
                        f"range {recovery_min}..{recovery_max}"
                    )
                else:
                    hard_errors.append(f"{outside_detail} and trusted recovery range {recovery_min}..{recovery_max}")
            elif margin < required_margin:
                recoverable_errors.append(
                    f"{motor}: position {raw_int} is only {margin} counts from calibrated limit "
                    f"{range_min}..{range_max} (need at least {required_margin})"
                )

        if hard_errors:
            raise RuntimeError(
                "Unsafe startup pose; torque will remain disabled. Position feedback is outside the "
                "trusted calibrated range:\n- " + "\n- ".join(hard_errors)
            )

        if recoverable_errors:
            reason = (
                "Recovery required: the torque-free arm is outside or near its calibrated operating range, "
                "but its feedback remains inside the guarded single-turn recovery envelope. Torque remains "
                "disabled; only a fully prevalidated return_safe trajectory may energize the motors:\n- "
                + "\n- ".join(recoverable_errors)
            )
            if allow_recovery:
                self._recovery_reason = reason
                return True
            raise RuntimeError(
                "Unsafe startup pose; torque will remain disabled. Support the structure, move each "
                "reported joint away from its hard stop, then retry:\n- " + "\n- ".join(recoverable_errors)
            )

        return False

    def _recovery_envelope_bounds(self, motor: str, cal: Any) -> tuple[int, int]:
        """Return a bounded non-wrapping raw envelope for torque-off recovery."""
        if self._bus is None:
            raise RuntimeError("Cannot calculate recovery bounds without a motor bus.")
        motor_config = self._bus.motors[motor]
        resolution = int(self._bus.model_resolution_table[motor_config.model])
        resolution_max = resolution - 1
        max_outside = max(1, int(resolution * self._RECOVERY_MAX_OUTSIDE_RANGE_RATIO))
        wrap_guard = max(1, int(resolution * self._RECOVERY_ENCODER_WRAP_GUARD_RATIO))
        range_min = int(cal.range_min)
        range_max = int(cal.range_max)
        return (
            min(range_min, max(wrap_guard, range_min - max_outside)),
            max(range_max, min(resolution_max - wrap_guard, range_max + max_outside)),
        )

    def read_recovery_start(self) -> dict[str, float]:
        """Return a stable, normalized recovery start while torque stays off."""
        if not self._connected or self._bus is None:
            raise RuntimeError("Motor bus is not connected for recovery.")
        if self._startup_state is not MotorStartupState.RECOVERY_REQUIRED:
            raise RuntimeError(f"Motor recovery is unavailable in startup state '{self._startup_state.value}'.")

        self._release_torque_for_manual_motion(strict=True)
        present_raw = self._read_stable_present_raw(list(self._bus.motors))
        self._validate_startup_positions(present_raw, allow_recovery=True)
        return {
            motor: self._raw_to_normalized(motor, float(raw), self._bus.calibration[motor])
            for motor, raw in present_raw.items()
        }

    def prepare_recovery(self, frames: list[dict[str, float]]) -> dict[str, float]:
        """Validate an entire recovery path, then enable torque at the current pose.

        This method must run before the motion control thread starts. No motor is
        energized until stable feedback, every frame, and the final target have
        all passed validation. Goal_Position is then seeded to the latest stable
        Present_Position and verified before torque is enabled.
        """
        if not self._connected or self._bus is None:
            raise RuntimeError("Motor bus is not connected for recovery.")
        if self._startup_state is not MotorStartupState.RECOVERY_REQUIRED:
            raise RuntimeError(f"Motor recovery is unavailable in startup state '{self._startup_state.value}'.")

        motors = list(self._bus.motors)
        self._release_torque_for_manual_motion(strict=True)
        present_raw = self._read_stable_present_raw(motors)
        self._validate_recovery_plan(present_raw, frames)

        try:
            self._expand_hardware_limits_for_recovery(present_raw)
            self._configure_recovery_motion_profile(motors)
            for motor, raw in present_raw.items():
                self._write_register_or_raise(
                    "Goal_Position",
                    motor,
                    int(raw),
                    normalize=False,
                )
            for motor in motors:
                self._write_register_or_raise("Lock", motor, 1)
                self._write_register_or_raise("Torque_Enable", motor, 1)
        except Exception:
            self._release_torque_for_manual_motion(strict=False)
            self._restore_normal_motion_profile(strict=False)
            self._restore_calibrated_hardware_limits(strict=False)
            raise

        self._startup_state = MotorStartupState.RECOVERING
        logger.info(
            "hal.recovery_torque_enabled",
            present_raw={motor: int(raw) for motor, raw in present_raw.items()},
            frame_count=len(frames),
        )
        return {
            motor: self._raw_to_normalized(motor, float(raw), self._bus.calibration[motor])
            for motor, raw in present_raw.items()
        }

    def complete_recovery(self) -> None:
        """Promote a completed recovery only after the final pose is safe."""
        if self._bus is None or self._startup_state is not MotorStartupState.RECOVERING:
            raise RuntimeError("No motor recovery is currently active.")
        final_raw = self._read_stable_present_raw(list(self._bus.motors))
        self._validate_startup_positions(final_raw)
        self._restore_normal_motion_profile(strict=True)
        self._restore_calibrated_hardware_limits(strict=True)
        self._startup_state = MotorStartupState.READY
        self._recovery_reason = None
        logger.info(
            "hal.recovery_complete",
            final_raw={motor: int(raw) for motor, raw in final_raw.items()},
            torque_held=True,
        )

    def abort_recovery(self) -> None:
        """Fail closed after an incomplete recovery."""
        if self._bus is None:
            return
        if self._startup_state is MotorStartupState.READY:
            return
        self._release_torque_for_manual_motion(strict=False)
        self._restore_normal_motion_profile(strict=False)
        self._restore_calibrated_hardware_limits(strict=False)
        self._startup_state = MotorStartupState.RECOVERY_REQUIRED
        if not self._recovery_reason:
            self._recovery_reason = "Recovery did not reach the verified return_safe target."
        logger.warning("hal.recovery_aborted", reason=self._recovery_reason)

    def _configure_recovery_motion_profile(self, motors: list[str]) -> None:
        """Install and verify a guarded STS position profile before torque-on."""
        if self._bus is None:
            raise RuntimeError("Cannot configure recovery profile without a motor bus.")

        for motor in motors:
            # Track before writing so a partial profile is restored on failure.
            self._recovery_profile_motors.add(motor)
            self._write_register_or_raise(
                "Acceleration",
                motor,
                self._RECOVERY_ACCELERATION_RAW,
                normalize=False,
            )
            self._write_register_or_raise("Goal_Time", motor, 0, normalize=False)
            self._write_register_or_raise(
                "Goal_Velocity",
                motor,
                self._RECOVERY_SPEED_RAW,
                normalize=False,
            )

        logger.info(
            "hal.recovery_motion_profile_verified",
            motors=motors,
            acceleration_raw=self._RECOVERY_ACCELERATION_RAW,
            speed_raw=self._RECOVERY_SPEED_RAW,
        )

    def _restore_normal_motion_profile(self, *, strict: bool) -> None:
        """Restore normal SRAM motion fields after recovery or abort."""
        if self._bus is None or not self._recovery_profile_motors:
            return

        failures: list[str] = []
        restored: list[str] = []
        for motor in sorted(self._recovery_profile_motors):
            try:
                self._write_register_or_raise(
                    "Acceleration",
                    motor,
                    self._NORMAL_ACCELERATION_RAW,
                    normalize=False,
                )
                self._write_register_or_raise("Goal_Time", motor, 0, normalize=False)
                self._write_register_or_raise("Goal_Velocity", motor, 0, normalize=False)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{motor}: {exc}")
            else:
                restored.append(motor)

        self._recovery_profile_motors.difference_update(restored)
        if restored:
            logger.info("hal.normal_motion_profile_restored", motors=restored)
        if failures:
            detail = "; ".join(failures)
            logger.error("hal.normal_motion_profile_restore_failed", detail=detail)
            if strict:
                raise RuntimeError(
                    "Recovery reached its target, but the normal motor profile could not be restored: " + detail
                )

    def _expand_hardware_limits_for_recovery(self, present_raw: dict[str, int]) -> None:
        """Temporarily include a verified recovery start in servo limits.

        Goal_Position is seeded only after these EPROM-backed limits read back.
        This prevents an out-of-range seed from being clipped to the calibrated
        boundary when torque is enabled.
        """
        if self._bus is None:
            raise RuntimeError("Cannot expand recovery limits without a motor bus.")

        expanded: dict[str, dict[str, int]] = {}
        for motor, raw in present_raw.items():
            cal = self._bus.calibration[motor]
            range_min = int(cal.range_min)
            range_max = int(cal.range_max)
            recovery_min = min(range_min, int(raw))
            recovery_max = max(range_max, int(raw))
            if recovery_min == range_min and recovery_max == range_max:
                continue

            # Track before the first limit write so a partially successful
            # expansion is still restored by the exception path.
            self._recovery_expanded_limit_motors.add(motor)
            self._write_register_or_raise("Lock", motor, 0, normalize=False)
            if recovery_min != range_min:
                self._write_register_or_raise(
                    "Min_Position_Limit",
                    motor,
                    recovery_min,
                    normalize=False,
                )
            if recovery_max != range_max:
                self._write_register_or_raise(
                    "Max_Position_Limit",
                    motor,
                    recovery_max,
                    normalize=False,
                )
            expanded[motor] = {"min": recovery_min, "max": recovery_max}

        if expanded:
            logger.info("hal.recovery_hardware_limits_expanded", motors=expanded)

    def _restore_calibrated_hardware_limits(self, *, strict: bool) -> None:
        """Restore temporary recovery limits while preserving fail-closed state."""
        if self._bus is None or not self._recovery_expanded_limit_motors:
            return

        failures: list[str] = []
        restored: list[str] = []
        for motor in sorted(self._recovery_expanded_limit_motors):
            cal = self._bus.calibration[motor]
            try:
                self._write_register_or_raise("Lock", motor, 0, normalize=False)
                self._write_register_or_raise(
                    "Min_Position_Limit",
                    motor,
                    int(cal.range_min),
                    normalize=False,
                )
                self._write_register_or_raise(
                    "Max_Position_Limit",
                    motor,
                    int(cal.range_max),
                    normalize=False,
                )
                self._write_register_or_raise("Lock", motor, 1, normalize=False)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{motor}: {exc}")
            else:
                restored.append(motor)

        self._recovery_expanded_limit_motors.difference_update(restored)
        if restored:
            logger.info("hal.recovery_hardware_limits_restored", motors=restored)
        if failures:
            detail = "; ".join(failures)
            logger.error("hal.recovery_hardware_limit_restore_failed", detail=detail)
            if strict:
                raise RuntimeError(
                    "Recovery reached its target, but calibrated hardware limits could not be restored: " + detail
                )

    def _read_stable_present_raw(self, motors: list[str]) -> dict[str, int]:
        if self._bus is None:
            raise RuntimeError("Cannot read recovery positions without a motor bus.")

        samples: list[dict[str, int]] = []
        for sample_idx in range(self._RECOVERY_STABLE_SAMPLE_COUNT):
            raw = self._bus.sync_read(
                "Present_Position",
                motors,
                normalize=False,
            )
            missing = [motor for motor in motors if motor not in raw]
            if missing:
                raise RuntimeError(
                    f"Recovery position feedback is incomplete; missing {missing}. Torque remains disabled."
                )
            samples.append({motor: int(raw[motor]) for motor in motors})
            if sample_idx + 1 < self._RECOVERY_STABLE_SAMPLE_COUNT:
                time.sleep(self._RECOVERY_STABLE_SAMPLE_INTERVAL_S)

        unstable: list[str] = []
        for motor in motors:
            values = [sample[motor] for sample in samples]
            spread = max(values) - min(values)
            if spread > self._RECOVERY_STABLE_SPREAD_COUNTS:
                unstable.append(f"{motor}: readings {min(values)}..{max(values)} spread {spread}")
        if unstable:
            raise RuntimeError(
                "Recovery position feedback is still moving or unstable; torque remains disabled:\n- "
                + "\n- ".join(unstable)
            )
        return samples[-1]

    def _validate_recovery_plan(
        self,
        present_raw: dict[str, int],
        frames: list[dict[str, float]],
    ) -> None:
        if self._bus is None:
            raise RuntimeError("Cannot validate recovery without a motor bus.")
        if not frames:
            raise RuntimeError("Recovery path is empty; torque remains disabled.")

        motors = list(self._bus.motors)
        self._validate_startup_positions(present_raw, allow_recovery=True)

        raw_frames: list[dict[str, int]] = []
        for index, frame in enumerate(frames):
            missing = [motor for motor in motors if motor not in frame]
            if missing:
                raise RuntimeError(f"Recovery frame {index} is incomplete; missing {missing}. Torque remains disabled.")
            if any(not math.isfinite(float(frame[motor])) for motor in motors):
                raise RuntimeError(f"Recovery frame {index} contains a non-finite position. Torque remains disabled.")
            raw_frames.append(
                {
                    motor: self._normalized_to_raw(
                        motor,
                        float(frame[motor]),
                        self._bus.calibration[motor],
                    )
                    for motor in motors
                }
            )

        final_raw = raw_frames[-1]
        self._validate_startup_positions(final_raw)

        errors: list[str] = []
        for motor in motors:
            cal = self._bus.calibration[motor]
            start = int(present_raw[motor])
            goal = int(final_raw[motor])
            direction = 1 if goal > start else -1 if goal < start else 0
            previous = start
            path_min = min(start, goal)
            path_max = max(start, goal)
            recovery_min, recovery_max = self._recovery_envelope_bounds(motor, cal)

            for index, raw_frame in enumerate(raw_frames):
                value = int(raw_frame[motor])
                if not recovery_min <= value <= recovery_max:
                    errors.append(
                        f"{motor}: frame {index} position {value} is outside guarded recovery range "
                        f"{recovery_min}..{recovery_max}"
                    )
                    break
                if not path_min <= value <= path_max:
                    errors.append(
                        f"{motor}: frame {index} position {value} leaves verified start-to-target "
                        f"envelope {path_min}..{path_max}"
                    )
                    break
                step = value - previous
                if abs(step) > self._RECOVERY_MAX_FRAME_STEP_COUNTS:
                    errors.append(
                        f"{motor}: frame {index} jumps {abs(step)} counts; maximum is "
                        f"{self._RECOVERY_MAX_FRAME_STEP_COUNTS}"
                    )
                    break
                if direction > 0 and (value < previous or value > goal):
                    errors.append(f"{motor}: frame {index} does not move monotonically toward safe target")
                    break
                if direction < 0 and (value > previous or value < goal):
                    errors.append(f"{motor}: frame {index} does not move monotonically toward safe target")
                    break
                if direction == 0 and value != goal:
                    errors.append(f"{motor}: frame {index} moves a joint that should remain still")
                    break
                previous = value

            if previous != goal:
                errors.append(f"{motor}: recovery path does not end at verified target {goal}")

        if errors:
            raise RuntimeError("Recovery path validation failed; torque remains disabled:\n- " + "\n- ".join(errors))

        logger.info(
            "hal.recovery_plan_verified",
            start_raw={motor: int(raw) for motor, raw in present_raw.items()},
            target_raw={motor: int(raw) for motor, raw in final_raw.items()},
            frame_count=len(frames),
        )

    def _raw_to_normalized(self, motor: str, raw: float, cal: Any) -> float:
        """Convert recovery feedback without display-oriented rounding."""
        if self._config.use_degrees:
            if self._bus is None:
                raise RuntimeError("Cannot convert recovery positions without a motor bus.")
            model = self._bus.motors[motor].model
            resolution_max = int(self._bus.model_resolution_table[model]) - 1
            midpoint = (float(cal.range_min) + float(cal.range_max)) / 2.0
            return (raw - midpoint) * 360.0 / resolution_max
        span = float(cal.range_max - cal.range_min)
        value = ((raw - float(cal.range_min)) / span) * 200.0 - 100.0
        return -value if bool(cal.drive_mode) else value

    def _normalized_to_raw(self, motor: str, value: float, cal: Any) -> int:
        if self._bus is None:
            raise RuntimeError("Cannot convert recovery positions without a motor bus.")
        model = self._bus.motors[motor].model
        resolution_max = int(self._bus.model_resolution_table[model]) - 1
        if self._config.use_degrees:
            midpoint = (float(cal.range_min) + float(cal.range_max)) / 2.0
            return round((value * resolution_max / 360.0) + midpoint)
        normalized = -value if bool(cal.drive_mode) else value
        return round(((normalized + 100.0) / 200.0) * (cal.range_max - cal.range_min) + cal.range_min)

    def _calibration_path(self) -> Any:
        return self._config.calibration_dir / f"{self._config.lamp_id}.json"

    @staticmethod
    def _raw_to_degrees(raw: float, cal: Any) -> float:
        mid = (cal.range_min + cal.range_max) / 2
        return round((raw - mid) * 360 / 4095, 1)

    def _fallback_half_turn_home(self, name: str, cal: Any) -> float:
        half = 4095 / 2
        span = cal.range_max - cal.range_min
        edge_distance = min(half - cal.range_min, cal.range_max - half)

        if span < self._MIN_VALID_RANGE_COUNTS:
            logger.warning(
                "hal.calibration_home_fallback_narrow_range",
                joint=name,
                range_min=cal.range_min,
                range_max=cal.range_max,
                span=span,
            )
            return 0.0

        if edge_distance < span * self._HOME_EDGE_MARGIN_RATIO:
            logger.warning(
                "hal.calibration_home_fallback_offcenter",
                joint=name,
                range_min=cal.range_min,
                range_max=cal.range_max,
                half=half,
                edge_distance=round(edge_distance, 1),
                span=span,
            )
            return 0.0

        return self._raw_to_degrees(half, cal)

    def _load_calibration_data(self) -> dict | None:
        import json

        path = self._calibration_path()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            logger.warning("calibration.load_failed", path=str(path))
            return None

    def _calibration_from_data(self, data: dict | None) -> dict | None:
        if not data:
            return None

        fields = ("id", "drive_mode", "homing_offset", "range_min", "range_max")
        result: dict[str, MotorCalibration] = {}
        for name, entry in data.items():
            if name not in self._config.motors or not isinstance(entry, dict):
                continue
            try:
                result[name] = MotorCalibration(**{field: entry[field] for field in fields})
            except Exception:
                logger.warning("calibration.entry_invalid", motor=name, exc_info=True)
        return result or None

    def _load_calibration(self) -> dict | None:
        return self._calibration_from_data(self._load_calibration_data())

    def _save_calibration(self, calibration: dict, *, neutral_raw: dict[str, float] | None = None) -> None:
        import json

        path = self._calibration_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "_meta": {
                "schema_version": self._CALIBRATION_SCHEMA_VERSION,
                "sts3215_single_turn_verified": True,
            }
        }
        for name, cal in calibration.items():
            data[name] = {
                "id": cal.id,
                "drive_mode": cal.drive_mode,
                "homing_offset": cal.homing_offset,
                "range_min": cal.range_min,
                "range_max": cal.range_max,
            }
            if neutral_raw and name in neutral_raw:
                raw = int(neutral_raw[name])
                data[name]["neutral_raw"] = raw
                data[name]["neutral_degrees"] = self._raw_to_degrees(raw, cal)
        path.write_text(json.dumps(data, indent=2))
        logger.info("calibration.saved_to_file", path=str(path))
