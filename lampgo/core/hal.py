"""Hardware Abstraction Layer for the LampGo Feetech motor bus.

The motor-bus lifecycle and calibration flow are inspired by LeLamp's
LeLampFollower runtime from humancomputerlab/LeLamp, which is licensed under
GPL-3.0. Low-level Feetech transport is provided by lerobot.
"""

from __future__ import annotations

import time
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


class HardwareAbstraction:
    """Thin wrapper around the Feetech motor bus.

    Coordinates connect, calibration, configuration, and sync I/O for LampGo's
    five motor joints.
    """

    _HOME_EDGE_MARGIN_RATIO = 0.15
    _MIN_VALID_RANGE_COUNTS = 80
    _RECOVERY_LIMIT_MARGIN_COUNTS = 96

    def __init__(self, config: DeviceConfig) -> None:
        self._config = config
        self._bus: Any | None = None
        self._connected = False
        self._status_error_motors: set[str] = set()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self, calibrate: bool = True, *, configure: bool = True) -> None:
        if self._connected:
            raise RuntimeError("HAL already connected")

        if not LEROBOT_AVAILABLE:
            logger.info("hal.connect: stub mode (no lerobot)")
            self._connected = True
            return

        norm_mode = MotorNormMode.DEGREES if self._config.use_degrees else MotorNormMode.RANGE_M100_100
        motors = {
            name: Motor(mc.id, mc.model, norm_mode)
            for name, mc in self._config.motors.items()
        }
        calibration = self._load_calibration()

        self._bus = FeetechMotorsBus(
            port=self._config.motor_port,
            motors=motors,
            calibration=calibration,
        )
        self._bus.connect(handshake=False)
        self._verify_expected_motors()

        if calibration:
            logger.info("hal.calibration_loaded", lamp_id=self._config.lamp_id)
        elif calibrate:
            logger.info("Motor bus not calibrated — running interactive calibration")
            self.calibrate()

        if configure:
            self._configure()
        self._connected = True
        logger.info("hal.connected", port=self._config.motor_port)

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self._bus is not None:
            try:
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
        logger.info("hal.disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

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
        sw.start_address = 42   # Goal_Position register address
        sw.data_length = 4      # 2 B position + 2 B time (consecutive registers)

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
        # Prefer per-motor explicit register writes first (more observable and
        # robust across mixed firmware), then call bus-level helper as fallback.
        for motor in self._bus.motors:
            self._safe_bus_write("Lock", motor, 0)
            self._safe_bus_write("Torque_Enable", motor, 0)
        try:
            self._bus.disable_torque()
        except Exception:
            logger.warning("hal.disable_torque_bus_call_failed", exc_info=True)
        logger.info("hal.torque_disabled")

    def enable_torque(self) -> None:
        """Enable motor torque so joints hold current pose."""
        if not self._connected:
            raise RuntimeError("HAL not connected")
        if self._bus is None:
            logger.info("hal.enable_torque: stub mode")
            return
        for motor in self._bus.motors:
            self._safe_bus_write("Lock", motor, 1)
            self._safe_bus_write("Torque_Enable", motor, 1)
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

        existing = self._load_calibration()
        if existing:
            choice = input(
                f"Press ENTER to use existing calibration for '{self._config.lamp_id}', "
                "or type 'c' to re-calibrate: "
            )
            if choice.strip().lower() != "c":
                self._bus.write_calibration(existing)
                logger.info("calibration.loaded", lamp_id=self._config.lamp_id)
                return

        logger.info("calibration.start")
        self._bus.disable_torque()
        for motor in self._bus.motors:
            self._bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)

        input("Move arm to the middle of its range of motion and press ENTER...")
        homing_offsets = self._bus.set_half_turn_homings()

        for motor_name, expected in homing_offsets.items():
            actual = self._bus.read("Homing_Offset", motor_name, normalize=False)
            if actual != expected:
                logger.warning(
                    "calibration.homing_offset_mismatch",
                    motor=motor_name, expected=expected, actual=actual,
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

        calibration: dict[str, MotorCalibration] = {}
        for motor_name, m in self._bus.motors.items():
            calibration[motor_name] = MotorCalibration(
                id=m.id,
                drive_mode=0,
                homing_offset=homing_offsets[motor_name],
                range_min=range_mins[motor_name],
                range_max=range_maxes[motor_name],
            )

        self._bus.write_calibration(calibration)
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

    def _configure(self) -> None:
        if self._bus is None:
            return

        healthy_motors = [m for m in self._bus.motors if m not in self._status_error_motors]
        if len(healthy_motors) != len(self._bus.motors):
            logger.warning(
                "hal.configure_skipping_status_error_motors",
                skipped=sorted(self._status_error_motors),
            )
        if not healthy_motors:
            logger.warning("hal.configure_skipped_all_motors")
            return

        for motor in healthy_motors:
            self._safe_bus_write("Torque_Enable", motor, 0)
            self._safe_bus_write("Lock", motor, 0)

        self._ensure_hardware_calibration()

        for motor in healthy_motors:
            # Configure each motor independently so one flaky status packet
            # does not block the whole arm from starting up.
            self._safe_bus_write("Return_Delay_Time", motor, 0)
            if getattr(self._bus, "protocol_version", None) == 0:
                self._safe_bus_write("Maximum_Acceleration", motor, 50)
            self._safe_bus_write("Acceleration", motor, 50)
            self._safe_bus_write("Operating_Mode", motor, OperatingMode.POSITION.value)
            self._safe_bus_write("P_Coefficient", motor, 16)
            self._safe_bus_write("I_Coefficient", motor, 0)
            self._safe_bus_write("D_Coefficient", motor, 32)

        self._seed_goal_positions_to_present(healthy_motors)
        for motor in healthy_motors:
            self._safe_bus_write("Lock", motor, 1)
            self._safe_bus_write("Torque_Enable", motor, 1)

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

    def _ensure_hardware_calibration(self) -> None:
        if self._bus is None or not self._bus.calibration:
            return
        try:
            if self._bus.is_calibrated:
                logger.info("hal.calibration_already_active", lamp_id=self._config.lamp_id)
                return
            self._bus.write_calibration(self._bus.calibration)
            logger.info("hal.calibration_applied", lamp_id=self._config.lamp_id)
        except Exception:
            logger.warning(
                "hal.calibration_apply_failed",
                lamp_id=self._config.lamp_id,
                exc_info=True,
            )

    def _seed_goal_positions_to_present(self, motors: list[str]) -> None:
        if self._bus is None or not motors:
            return

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

        if not present_raw:
            logger.warning("hal.goal_seed_no_present_position", motors=motors)
            return

        self._expand_hardware_limits_to_present(present_raw)

        seeded: dict[str, int] = {}
        failed: list[str] = []
        for motor, raw in present_raw.items():
            raw_int = int(raw)
            if self._safe_bus_write("Goal_Position", motor, raw_int, normalize=False):
                seeded[motor] = raw_int
            else:
                failed.append(motor)

        if seeded:
            logger.info("hal.goal_seeded_to_present", present_raw=seeded)
        if failed:
            logger.warning("hal.goal_seed_failed", motors=failed)

    def _expand_hardware_limits_to_present(self, present_raw: dict[str, float]) -> None:
        if self._bus is None or not self._bus.calibration:
            return

        for motor, raw in present_raw.items():
            cal = self._bus.calibration.get(motor)
            if cal is None:
                continue

            raw_int = int(raw)
            recovery_min = max(0, min(cal.range_min, raw_int - self._RECOVERY_LIMIT_MARGIN_COUNTS))
            recovery_max = min(4095, max(cal.range_max, raw_int + self._RECOVERY_LIMIT_MARGIN_COUNTS))
            if recovery_min == cal.range_min and recovery_max == cal.range_max:
                continue

            self._safe_bus_write("Min_Position_Limit", motor, recovery_min, normalize=False)
            self._safe_bus_write("Max_Position_Limit", motor, recovery_max, normalize=False)
            logger.warning(
                "hal.recovery_limits_expanded",
                motor=motor,
                present_raw=raw_int,
                range_min=cal.range_min,
                range_max=cal.range_max,
                recovery_min=recovery_min,
                recovery_max=recovery_max,
            )

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
        data = {}
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
