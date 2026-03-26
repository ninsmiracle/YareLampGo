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

    def __init__(self, config: DeviceConfig) -> None:
        self._config = config
        self._bus: Any | None = None
        self._connected = False
        self._status_error_motors: set[str] = set()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self, calibrate: bool = True) -> None:
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

    def write_positions(self, positions: dict[str, float]) -> None:
        if not self._connected:
            raise RuntimeError("HAL not connected")
        if self._bus is None:
            return

        self._bus.sync_write("Goal_Position", positions)

    def read_health(self) -> DeviceHealth:
        if not self._connected:
            return DeviceHealth.DISCONNECTED
        if self._bus is None:
            return DeviceHealth.DEGRADED
        return DeviceHealth.OK

    def get_calibration_home(self) -> dict[str, float] | None:
        """Compute the degree-mode value that corresponds to the physical calibration midpoint.

        In lerobot DEGREES mode: degrees = (val - mid) * 360 / max_res
        where mid = (range_min + range_max) / 2.
        The homing midpoint (where the user placed the arm) is at val = max_res/2 (2047).
        So home_degrees = (2047 - mid) * 360 / max_res.
        """
        calibration = self._load_calibration()
        if not calibration:
            return None
        max_res = 4095
        half = max_res / 2
        home: dict[str, float] = {}
        for name, cal in calibration.items():
            mid = (cal.range_min + cal.range_max) / 2
            span = cal.range_max - cal.range_min
            edge_distance = min(half - cal.range_min, cal.range_max - half)

            # Guardrail: if calibration midpoint (half-turn) sits too close to a range edge,
            # homing there is usually unsafe (bad midpoint placement during calibration).
            if span < self._MIN_VALID_RANGE_COUNTS:
                logger.warning(
                    "hal.calibration_home_fallback_narrow_range",
                    joint=name,
                    range_min=cal.range_min,
                    range_max=cal.range_max,
                    span=span,
                )
                home[name] = 0.0
                continue

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
                home[name] = 0.0
                continue

            home[name] = round((half - mid) * 360 / max_res, 1)
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
        self._save_calibration(calibration)
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
            # Configure each motor independently so one flaky status packet
            # does not block the whole arm from starting up.
            self._safe_bus_write("Torque_Enable", motor, 0)
            self._safe_bus_write("Lock", motor, 0)
            self._safe_bus_write("Return_Delay_Time", motor, 0)
            if getattr(self._bus, "protocol_version", None) == 0:
                self._safe_bus_write("Maximum_Acceleration", motor, 254)
            self._safe_bus_write("Acceleration", motor, 254)
            self._safe_bus_write("Operating_Mode", motor, OperatingMode.POSITION.value)
            self._safe_bus_write("P_Coefficient", motor, 16)
            self._safe_bus_write("I_Coefficient", motor, 0)
            self._safe_bus_write("D_Coefficient", motor, 32)
            self._safe_bus_write("Lock", motor, 1)
            self._safe_bus_write("Torque_Enable", motor, 1)

    def _safe_bus_write(self, data_name: str, motor: str, value: Any) -> bool:
        if self._bus is None:
            return False
        try:
            self._bus.write(data_name, motor, value)
            return True
        except Exception:
            logger.warning(
                "hal.configure_write_failed",
                motor=motor,
                register=data_name,
                value=value,
                exc_info=True,
            )
            return False

    def _calibration_path(self) -> Any:
        return self._config.calibration_dir / f"{self._config.lamp_id}.json"

    def _load_calibration(self) -> dict | None:
        import json

        path = self._calibration_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            result: dict[str, MotorCalibration] = {}
            for name, entry in data.items():
                result[name] = MotorCalibration(**entry)
            return result
        except Exception:
            logger.warning("calibration.load_failed", path=str(path))
            return None

    def _save_calibration(self, calibration: dict) -> None:
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
        path.write_text(json.dumps(data, indent=2))
        logger.info("calibration.saved_to_file", path=str(path))
