"""Tests for HAL calibration-home derivation safeguards and motor checks."""
from types import SimpleNamespace

from lampgo.core.config import DeviceConfig
from lampgo.core.hal import HardwareAbstraction


def test_get_calibration_home_uses_midpoint_when_reliable(monkeypatch):
    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    calibration = {
        "base_pitch": SimpleNamespace(range_min=1200, range_max=2800),
    }
    monkeypatch.setattr(hal, "_load_calibration", lambda: calibration)

    home = hal.get_calibration_home()

    assert home is not None
    assert home["base_pitch"] == 4.2


def test_get_calibration_home_fallbacks_to_zero_when_half_turn_near_edge(monkeypatch):
    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    calibration = {
        # half-turn (2047.5) is very close to range_max -> unsafe midpoint
        "base_pitch": SimpleNamespace(range_min=1377, range_max=2056),
    }
    monkeypatch.setattr(hal, "_load_calibration", lambda: calibration)

    home = hal.get_calibration_home()

    assert home is not None
    assert home["base_pitch"] == 0.0


def test_verify_expected_motors_accepts_status_error_when_model_matches():
    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))

    class FakePacketHandler:
        def ping(self, port_handler, motor_id):
            return 777, 0, 8

        def getRxPacketError(self, error):
            return "[RxPacketError] OverEle error!"

    hal._bus = SimpleNamespace(
        motors={"base_pitch": SimpleNamespace(id=2, model="sts3215")},
        ids=[2],
        model_number_table={"sts3215": 777},
        packet_handler=FakePacketHandler(),
        port_handler=object(),
        _is_comm_success=lambda comm: comm == 0,
        _is_error=lambda error: error != 0,
    )

    hal._verify_expected_motors()


def test_verify_expected_motors_rejects_missing_motor():
    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))

    class FakePacketHandler:
        def ping(self, port_handler, motor_id):
            return None, -1, 0

    hal._bus = SimpleNamespace(
        motors={"base_pitch": SimpleNamespace(id=2, model="sts3215")},
        ids=[2],
        model_number_table={"sts3215": 777},
        packet_handler=FakePacketHandler(),
        port_handler=object(),
        _is_comm_success=lambda comm: comm == 0,
        _is_error=lambda error: error != 0,
        _id_to_name=lambda motor_id: "base_pitch",
    )

    try:
        hal._verify_expected_motors()
        raise AssertionError("Expected motor verification to fail")
    except RuntimeError as exc:
        assert "Missing motor IDs" in str(exc)


def test_configure_skips_status_error_motors():
    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    writes: list[tuple[str, str, int]] = []

    hal._bus = SimpleNamespace(
        motors={
            "base_yaw": SimpleNamespace(id=1, model="sts3215"),
            "base_pitch": SimpleNamespace(id=2, model="sts3215"),
        },
        protocol_version=0,
        write=lambda data_name, motor, value: writes.append((data_name, motor, value)),
    )
    hal._status_error_motors = {"base_pitch"}

    hal._configure()

    configured_motors = {motor for _, motor, _ in writes}
    assert configured_motors == {"base_yaw"}


def test_configure_continues_after_single_register_write_failure():
    hal = HardwareAbstraction(DeviceConfig(motor_port="/dev/null"))
    writes: list[tuple[str, str, int]] = []

    def fake_write(data_name, motor, value):
        writes.append((data_name, motor, value))
        if data_name == "Lock" and value == 0:
            raise ConnectionError("Incorrect status packet!")

    hal._bus = SimpleNamespace(
        motors={"wrist_pitch": SimpleNamespace(id=5, model="sts3215")},
        protocol_version=0,
        write=fake_write,
    )

    hal._configure()

    written_registers = [data_name for data_name, _, _ in writes]
    assert "Lock" in written_registers
    assert "Operating_Mode" in written_registers
    assert "Torque_Enable" in written_registers
