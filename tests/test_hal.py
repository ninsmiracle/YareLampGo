"""Hardware abstraction tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _SafetyFakeBus:
    def __init__(
        self,
        *,
        phase: int = 0,
        present: int = 1800,
        calibration=None,
        ignore_phase_write: bool = False,
        ignore_goal_write: bool = False,
    ) -> None:
        self.motors = {"base_pitch": SimpleNamespace(model="sts3215")}
        self.model_resolution_table = {"sts3215": 4096}
        self.protocol_version = 0
        self.calibration = calibration or {}
        self.present = present
        self.ignore_phase_write = ignore_phase_write
        self.ignore_goal_write = ignore_goal_write
        self.values = {
            ("Phase", "base_pitch"): phase,
            ("Torque_Enable", "base_pitch"): 0,
            ("Lock", "base_pitch"): 0,
            ("Goal_Position", "base_pitch"): present - 1,
        }
        self.writes: list[tuple[str, str, int, bool]] = []

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
        del num_retry
        self.writes.append((data_name, motor, value, normalize))
        if data_name == "Phase" and self.ignore_phase_write:
            return
        if data_name == "Goal_Position" and self.ignore_goal_write:
            return
        self.values[(data_name, motor)] = value

    def read(self, data_name: str, motor: str, *, normalize: bool = True) -> int:
        del normalize
        if data_name == "Present_Position":
            return self.present
        return self.values[(data_name, motor)]

    def sync_read(self, data_name: str, motors: list[str], *, normalize: bool = True) -> dict[str, int]:
        assert data_name == "Present_Position"
        assert motors == ["base_pitch"]
        assert normalize is False
        return {"base_pitch": self.present}

    def disable_torque(self, motors: list[str], num_retry: int = 0) -> None:
        del num_retry
        for motor in motors:
            self.values[("Torque_Enable", motor)] = 0

    def write_calibration(self, calibration) -> None:
        self.calibration = calibration


def test_hal_configure_seeds_goal_position_before_enabling_torque() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    class FakeBus:
        def __init__(self) -> None:
            self.motors = {"base_pitch": SimpleNamespace(model="dummy")}
            self.model_resolution_table = {"dummy": 4096}
            self.protocol_version = 0
            self.calibration = {}
            self.writes: list[tuple[str, str, int, bool]] = []
            self.reads: list[tuple[str, str, bool]] = []
            self.values: dict[tuple[str, str], int] = {}

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
            self.values[(data_name, motor)] = value

        def read(self, data_name: str, motor: str, *, normalize: bool = True) -> int:
            self.reads.append((data_name, motor, normalize))
            assert motor == "base_pitch"
            if data_name == "Present_Position":
                assert normalize is False
                return 1739
            return self.values[(data_name, motor)]

        def sync_read(self, data_name: str, motors: list[str], *, normalize: bool = True) -> dict[str, int]:
            assert data_name == "Present_Position"
            assert motors == ["base_pitch"]
            assert normalize is False
            self.reads.append((data_name, "base_pitch", normalize))
            return {"base_pitch": 1739}

        def disable_torque(self, motors: list[str], num_retry: int = 0) -> None:
            del num_retry
            for motor in motors:
                self.values[("Torque_Enable", motor)] = 0

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = FakeBus()
    hal._bus = bus

    hal._configure()

    assert ("Present_Position", "base_pitch", False) in bus.reads
    assert ("Goal_Position", "base_pitch", 1739, False) in bus.writes
    assert ("Torque_Limit", "base_pitch", 800, False) in bus.writes

    disable_idx = bus.writes.index(("Torque_Enable", "base_pitch", 0, True))
    seed_idx = bus.writes.index(("Goal_Position", "base_pitch", 1739, False))
    torque_idx = bus.writes.index(("Torque_Enable", "base_pitch", 1, True))
    assert disable_idx < seed_idx < torque_idx


def test_hal_configure_refuses_present_position_outside_calibration_range() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    class FakeBus:
        def __init__(self) -> None:
            self.motors = {"base_pitch": SimpleNamespace(model="dummy")}
            self.model_resolution_table = {"dummy": 4096}
            self.protocol_version = 0
            self.calibration = {
                "base_pitch": SimpleNamespace(range_min=1739, range_max=3094, homing_offset=-562)
            }
            self.writes: list[tuple[str, str, int, bool]] = []
            self.values: dict[tuple[str, str], int] = {}

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
            self.values[(data_name, motor)] = value

        def read(self, data_name: str, motor: str, *, normalize: bool = True) -> int:
            assert motor == "base_pitch"
            if data_name == "Present_Position":
                assert normalize is False
                return 540
            return self.values[(data_name, motor)]

        def sync_read(self, data_name: str, motors: list[str], *, normalize: bool = True) -> dict[str, int]:
            assert data_name == "Present_Position"
            assert normalize is False
            return {"base_pitch": 540}

        def disable_torque(self, motors: list[str], num_retry: int = 0) -> None:
            del num_retry
            for motor in motors:
                self.values[("Torque_Enable", motor)] = 0

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = FakeBus()
    hal._bus = bus

    with pytest.raises(RuntimeError, match="outside calibrated range"):
        hal._configure()

    assert ("Torque_Limit", "base_pitch", 800, False) in bus.writes
    assert ("Torque_Enable", "base_pitch", 0, True) in bus.writes
    assert ("Goal_Position", "base_pitch", 540, False) not in bus.writes
    assert ("Torque_Enable", "base_pitch", 1, True) not in bus.writes


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


def test_hal_manual_motion_release_retries_and_verifies_torque_registers() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    class FakeBus:
        def __init__(self) -> None:
            self.motors = {"elbow_pitch": object()}
            self.writes: list[tuple[str, str, int, bool, int]] = []
            self.disable_calls: list[tuple[list[str], int]] = []

        def write(
            self,
            data_name: str,
            motor: str,
            value: int,
            *,
            normalize: bool = True,
            num_retry: int = 0,
        ) -> None:
            self.writes.append((data_name, motor, value, normalize, num_retry))

        def read(self, data_name: str, motor: str, *, normalize: bool = True) -> int:
            assert motor == "elbow_pitch"
            assert normalize is False
            assert data_name in {"Torque_Enable", "Lock"}
            return 0

        def disable_torque(self, motors, num_retry: int = 0) -> None:
            self.disable_calls.append((list(motors), num_retry))

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = FakeBus()
    hal._bus = bus

    hal._release_torque_for_manual_motion(strict=True)

    assert ("Torque_Enable", "elbow_pitch", 0, True, 3) in bus.writes
    assert ("Lock", "elbow_pitch", 0, True, 3) in bus.writes
    assert bus.disable_calls == [(["elbow_pitch"], 3)]


def test_hal_manual_motion_release_raises_when_register_stays_enabled() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    class FakeBus:
        motors = {"elbow_pitch": object()}

        def write(
            self,
            data_name: str,
            motor: str,
            value: int,
            *,
            normalize: bool = True,
            num_retry: int = 0,
        ) -> None:
            del data_name, motor, value, normalize, num_retry

        def read(self, data_name: str, motor: str, *, normalize: bool = True) -> int:
            assert motor == "elbow_pitch"
            assert normalize is False
            if data_name == "Torque_Enable":
                return 1
            if data_name == "Lock":
                return 0
            raise AssertionError(data_name)

        def disable_torque(self, motors, num_retry: int = 0) -> None:
            del motors, num_retry

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    hal._bus = FakeBus()

    with pytest.raises(RuntimeError, match="elbow_pitch"):
        hal._release_torque_for_manual_motion(strict=True)


def test_hal_opens_full_manual_calibration_range_before_user_moves() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    class FakeBus:
        def __init__(self) -> None:
            self.motors = {"elbow_pitch": SimpleNamespace(model="sts3215")}
            self.model_resolution_table = {"sts3215": 4096}
            self.values = {
                ("Homing_Offset", "elbow_pitch"): -1083,
                ("Min_Position_Limit", "elbow_pitch"): 1307,
                ("Max_Position_Limit", "elbow_pitch"): 2920,
            }
            self.writes: list[tuple[str, str, int, bool, int]] = []

        def write(
            self,
            data_name: str,
            motor: str,
            value: int,
            *,
            normalize: bool = True,
            num_retry: int = 0,
        ) -> None:
            self.writes.append((data_name, motor, value, normalize, num_retry))
            self.values[(data_name, motor)] = value

        def read(self, data_name: str, motor: str, *, normalize: bool = True) -> int:
            assert motor == "elbow_pitch"
            assert normalize is False
            return self.values[(data_name, motor)]

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = FakeBus()
    hal._bus = bus

    hal._open_manual_calibration_range()

    assert ("Homing_Offset", "elbow_pitch", 0, False, 3) in bus.writes
    assert ("Min_Position_Limit", "elbow_pitch", 0, False, 3) in bus.writes
    assert ("Max_Position_Limit", "elbow_pitch", 4095, False, 3) in bus.writes


def test_hal_clears_sts3215_multi_turn_phase_bit_and_preserves_other_bits() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = _SafetyFakeBus(phase=0x13)
    hal._bus = bus

    changed = hal._ensure_sts3215_single_turn(["base_pitch"])

    assert changed == {"base_pitch"}
    assert bus.values[("Phase", "base_pitch")] == 0x03
    assert ("Phase", "base_pitch", 0x03, False) in bus.writes


def test_hal_refuses_torque_when_sts3215_phase_bit_cannot_be_cleared() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = _SafetyFakeBus(phase=0x10, ignore_phase_write=True)
    hal._bus = bus

    with pytest.raises(RuntimeError, match="Cannot verify Phase"):
        hal._configure()

    assert ("Torque_Enable", "base_pitch", 1, True) not in bus.writes


def test_hal_refuses_legacy_calibration_without_single_turn_metadata() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    calibration = {
        "base_pitch": SimpleNamespace(range_min=1296, range_max=2279, homing_offset=1849)
    }
    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = _SafetyFakeBus(calibration=calibration)
    hal._bus = bus
    hal._load_calibration_data = lambda: {"base_pitch": {"range_min": 1296, "range_max": 2279}}

    with pytest.raises(RuntimeError, match="predates STS3215 single-turn"):
        hal._configure()

    assert ("Torque_Enable", "base_pitch", 1, True) not in bus.writes


def test_hal_refuses_startup_at_calibrated_edge_from_incident_log() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    calibration = {
        "base_pitch": SimpleNamespace(range_min=1296, range_max=2279, homing_offset=1849)
    }
    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = _SafetyFakeBus(present=2276, calibration=calibration)
    hal._bus = bus
    hal._load_calibration_data = lambda: {
        "_meta": {"schema_version": 2, "sts3215_single_turn_verified": True},
        "base_pitch": {"range_min": 1296, "range_max": 2279},
    }

    with pytest.raises(RuntimeError, match="only 3 counts from calibrated limit"):
        hal._configure()

    assert ("Goal_Position", "base_pitch", 2276, False) not in bus.writes
    assert ("Torque_Enable", "base_pitch", 1, True) not in bus.writes


def test_hal_refuses_torque_when_seeded_goal_does_not_read_back() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = _SafetyFakeBus(ignore_goal_write=True)
    hal._bus = bus

    with pytest.raises(RuntimeError, match="Cannot verify Goal_Position"):
        hal._configure()

    assert ("Torque_Enable", "base_pitch", 1, True) not in bus.writes


def test_hal_rejects_overflowed_and_edge_neutral_calibration() -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    hal._bus = _SafetyFakeBus()

    with pytest.raises(RuntimeError, match="outside single-turn"):
        hal._validate_recorded_calibration(
            {"base_pitch": 2047},
            {"base_pitch": 1296},
            {"base_pitch": 5000},
        )

    with pytest.raises(RuntimeError, match="too close to range edge"):
        hal._validate_recorded_calibration(
            {"base_pitch": 2276},
            {"base_pitch": 1296},
            {"base_pitch": 2279},
        )


def test_hal_final_neutral_confirmation_retries_without_discarding_calibration(
    monkeypatch, capsys
) -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    calibration = {
        "base_pitch": SimpleNamespace(range_min=1429, range_max=2158, homing_offset=0)
    }
    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = _SafetyFakeBus(calibration=calibration)
    positions = iter([2149, 2051])
    prompts: list[str] = []

    def sync_read(data_name: str, motors: list[str], *, normalize: bool = True) -> dict[str, int]:
        assert data_name == "Present_Position"
        assert motors == ["base_pitch"]
        assert normalize is False
        return {"base_pitch": next(positions)}

    bus.sync_read = sync_read
    hal._bus = bus
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "")

    final_raw = hal._wait_for_safe_neutral_pose({"base_pitch": 2051}, calibration)

    assert final_raw == {"base_pitch": 2051}
    assert len(prompts) == 2
    assert "only 9 counts from calibrated limit" in capsys.readouterr().out


def test_hal_final_pose_inside_limits_saves_even_when_not_exactly_neutral(
    monkeypatch, capsys
) -> None:
    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    calibration = {
        "elbow_pitch": SimpleNamespace(range_min=1422, range_max=2776, homing_offset=0)
    }
    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    bus = _SafetyFakeBus()
    bus.motors = {"elbow_pitch": SimpleNamespace(model="sts3215")}
    bus.calibration = calibration
    bus.sync_read = lambda *args, **kwargs: {"elbow_pitch": 2198}
    hal._bus = bus
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "")

    final_raw = hal._wait_for_safe_neutral_pose({"elbow_pitch": 2047}, calibration)

    assert final_raw == {"elbow_pitch": 2198}
    assert len(prompts) == 1
    output = capsys.readouterr().out
    assert "not close to its recorded neutral pose" in output
    assert "calibration will be saved" in output


def test_hal_saved_calibration_marks_single_turn_verification(tmp_path) -> None:
    import json

    from lampgo.core.config import DeviceConfig
    from lampgo.core.hal import HardwareAbstraction

    hal = HardwareAbstraction(
        DeviceConfig(motor_port="/dev/null", lamp_id="TEST", calibration_dir=tmp_path)
    )
    calibration = {
        "base_pitch": SimpleNamespace(
            id=2,
            drive_mode=0,
            homing_offset=1849,
            range_min=1296,
            range_max=2279,
        )
    }

    hal._save_calibration(calibration, neutral_raw={"base_pitch": 2047})

    data = json.loads((tmp_path / "TEST.json").read_text())
    assert data["_meta"] == {
        "schema_version": 2,
        "sts3215_single_turn_verified": True,
    }
