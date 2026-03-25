"""Hardware Abstraction Layer for the LampGo Feetech motor bus.

The motor-bus lifecycle and calibration flow are inspired by LeLamp's
LeLampFollower runtime from humancomputerlab/LeLamp, which is licensed under
GPL-3.0. Low-level Feetech transport is provided by lerobot.
"""

from __future__ import annotations

import time
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

    Responsibilities:
    - Connect / disconnect the serial bus
    - Read current joint positions
    - Write goal positions (no interpolation, no safety — just I/O)
    - Calibration workflow
    - Motor setup (ID assignment)
    """

    def __init__(self, config: DeviceConfig) -> None:
        self._config = config
        self._bus: Any | None = None
        self._connected = False

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
            name: Motor(mc.id, mc.model, norm_mode) for name, mc in self._config.motors.items()
        }

        calibration = self._load_calibration()
        self._bus = FeetechMotorsBus(
            port=self._config.motor_port,
            motors=motors,
            calibration=calibration,
        )
        self._bus.connect()

        if not self._bus.is_calibrated and calibrate:
            logger.info("Motor bus not calibrated — running interactive calibration")
            self.calibrate()

        self._configure_motors()
        self._connected = True
        logger.info("hal.connected", port=self._config.motor_port)

    def disconnect(self) -> None:
        if not self._connected:
            return
        if self._bus is not None:
            try:
                self._bus.disconnect(self._config.disable_torque_on_disconnect)
            except Exception:
                logger.exception("hal.disconnect: error during bus disconnect")
        self._connected = False
        self._bus = None
        logger.info("hal.disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    def read_positions(self) -> JointState:
        """Read present positions from all motors. Thread-safe for the control loop."""
        if not self._connected:
            raise RuntimeError("HAL not connected")

        if self._bus is None:
            return JointState(positions={name: 0.0 for name in self._config.motors})

        raw = self._bus.sync_read("Present_Position")
        return JointState(positions=dict(raw), timestamp=time.monotonic())

    def write_positions(self, positions: dict[str, float]) -> None:
        """Write goal positions. No interpolation, no safety — caller is responsible."""
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
        try:
            self._bus.sync_read("Present_Position")
            return DeviceHealth.OK
        except Exception:
            return DeviceHealth.DISCONNECTED

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def calibrate(self) -> None:
        """Interactive calibration flow for LampGo's motor bus."""
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
        """Interactive motor ID assignment — connect one motor at a time."""
        if self._bus is None:
            raise RuntimeError("Cannot setup motors in stub mode")
        for motor in reversed(list(self._bus.motors)):
            input(f"Connect only the '{motor}' motor and press ENTER.")
            self._bus.setup_motor(motor)
            print(f"  '{motor}' ID set to {self._bus.motors[motor].id}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _configure_motors(self) -> None:
        """Apply PID and operating mode settings for LampGo's motor bus."""
        if self._bus is None:
            return
        with self._bus.torque_disabled():
            self._bus.configure_motors()
            for motor in self._bus.motors:
                self._bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)
                self._bus.write("P_Coefficient", motor, 16)
                self._bus.write("I_Coefficient", motor, 0)
                self._bus.write("D_Coefficient", motor, 32)

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
