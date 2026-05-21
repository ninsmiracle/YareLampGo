"""Hardware abstraction tests."""

from __future__ import annotations

from types import SimpleNamespace


def test_hal_configure_seeds_goal_position_before_enabling_torque() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    class FakeBus:
        def __init__(self) -> None:
            self.motors = {"base_pitch": object()}
            self.protocol_version = 0
            self.calibration = {}
            self.writes: list[tuple[str, str, int, bool]] = []
            self.reads: list[tuple[str, str, bool]] = []
            self.goals: dict[str, int] = {}

        def write(
            self,
            data_name: str,
            motor: str,
            value: int,
            *,
            normalize: bool = True,
            num_retry: int = 0,
        ) -> None:
            self.writes.append((data_name, motor, value, normalize))
            if data_name == "Goal_Position":
                self.goals[motor] = value

        def read(self, data_name: str, motor: str, *, normalize: bool = True) -> int:
            self.reads.append((data_name, motor, normalize))
            assert motor == "base_pitch"
            assert normalize is False
            if data_name == "Goal_Position":
                return self.goals[motor]
            assert data_name == "Present_Position"
            return 1739

        def sync_read(self, data_name: str, motors: list[str], *, normalize: bool = True) -> dict[str, int]:
            assert data_name == "Present_Position"
            assert motors == ["base_pitch"]
            assert normalize is False
            self.reads.append((data_name, "base_pitch", normalize))
            return {"base_pitch": 1739}

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = FakeBus()
    hal._bus = bus

    hal._configure()

    assert ("Present_Position", "base_pitch", False) in bus.reads
    assert ("Goal_Position", "base_pitch", 1739, False) in bus.writes

    disable_idx = bus.writes.index(("Torque_Enable", "base_pitch", 0, True))
    seed_idx = bus.writes.index(("Goal_Position", "base_pitch", 1739, False))
    torque_idx = bus.writes.index(("Torque_Enable", "base_pitch", 1, True))
    assert disable_idx < seed_idx < torque_idx


def test_hal_configure_expands_limits_and_holds_when_present_outside_calibration_range() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    class FakeBus:
        def __init__(self) -> None:
            self.motors = {"base_pitch": object()}
            self.protocol_version = 0
            self.calibration = {
                "base_pitch": SimpleNamespace(range_min=1739, range_max=3094, homing_offset=-562)
            }
            self.writes: list[tuple[str, str, int, bool]] = []
            self.goals: dict[str, int] = {}

        @property
        def is_calibrated(self) -> bool:
            return True

        def write(
            self,
            data_name: str,
            motor: str,
            value: int,
            *,
            normalize: bool = True,
            num_retry: int = 0,
        ) -> None:
            self.writes.append((data_name, motor, value, normalize))
            if data_name == "Goal_Position":
                self.goals[motor] = value

        def read(self, data_name: str, motor: str, *, normalize: bool = True) -> int:
            assert motor == "base_pitch"
            assert normalize is False
            if data_name == "Goal_Position":
                return self.goals[motor]
            assert data_name == "Present_Position"
            return 540

        def sync_read(self, data_name: str, motors: list[str], *, normalize: bool = True) -> dict[str, int]:
            assert data_name == "Present_Position"
            assert normalize is False
            return {"base_pitch": 540}

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = FakeBus()
    hal._bus = bus

    hal._configure()

    assert ("Min_Position_Limit", "base_pitch", 444, False) in bus.writes
    assert ("Goal_Position", "base_pitch", 540, False) in bus.writes
    assert ("Torque_Enable", "base_pitch", 0, True) in bus.writes
    assert ("Torque_Enable", "base_pitch", 1, True) in bus.writes


def test_hal_calibration_home_uses_saved_neutral_degrees() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    hal._load_calibration_data = lambda: {
        "elbow_pitch": {
            "id": 3,
            "drive_mode": 0,
            "homing_offset": 1519,
            "range_min": 1909,
            "range_max": 3479,
            "neutral_raw": 2047,
            "neutral_degrees": -56.8,
        },
        "wrist_pitch": {
            "id": 5,
            "drive_mode": 0,
            "homing_offset": 1139,
            "range_min": 1095,
            "range_max": 2048,
            "neutral_raw": 2047,
            "neutral_degrees": 41.8,
        },
    }

    home = hal.get_calibration_home()

    assert home == {"elbow_pitch": -56.8, "wrist_pitch": 41.8}
