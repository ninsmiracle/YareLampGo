from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from lampgo.core.config import DeviceConfig
from lampgo.core.hal import MotorStartupState
from lampgo.core.p4_hal import P4ControlError, P4HardwareAbstraction
from lampgo.diagnostics import redact, support_bundle
from lampgo.p4_maintenance import P4Maintenance, calibration_preview
from lampgo.serial_guard import has_ping_reply, require_quiet_serial


def make_hal(tmp_path):
    data = {
        name: {
            "id": m.id,
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 1200,
            "range_max": 2800,
            "neutral_raw": 2047,
        }
        for name, m in DeviceConfig().motors.items()
    }
    (tmp_path / "T.json").write_text(json.dumps(data))
    hal = P4HardwareAbstraction(DeviceConfig(lamp_id="T", motor_transport="p4", calibration_dir=tmp_path), Mock())
    hal._connected = True
    hal._startup_state = MotorStartupState.RECOVERY_REQUIRED
    return hal


def plan(hal, start, count=150):
    return [
        {
            name: hal._raw_to_degrees(
                start[str(cal.servo_id)] + (2047 - start[str(cal.servo_id)]) * (i + 1) / count, cal
            )
            for name, cal in hal._calibration.items()
        }
        for i in range(count)
    ]


def test_natural_pose_prevalidates_then_sends_recovery_prepare(tmp_path):
    hal = make_hal(tmp_path)
    start = {str(i): 3000 if i == 2 else 2047 for i in range(1, 6)}
    hal._recovery_raw_start = start
    hal._submit_control = Mock(
        return_value={"ok": True, "startup_state": "recovering", "positions": start, "online_ids": [1, 2, 3, 4, 5]}
    )
    result = hal.prepare_recovery(plan(hal, start))
    assert set(result) == set(hal.motor_names)
    assert hal._submit_control.call_args.args[0] == "recovery_prepare"
    assert hal._submit_control.call_args.args[1]["targets"]["2"] == 2047


@pytest.mark.parametrize("failure", ["reverse", "jump", "missing", "nan", "unsafe_start", "edge_target"])
def test_bad_plan_never_sends_torque_or_prepare(tmp_path, failure):
    hal = make_hal(tmp_path)
    start = {str(i): 3000 if i == 2 else 2047 for i in range(1, 6)}
    hal._recovery_raw_start = start
    frames = plan(hal, start)
    if failure == "reverse":
        frames[60], frames[61] = frames[61], frames[60]
    elif failure == "jump":
        frames = frames[::20] + [frames[-1]]
    elif failure == "missing":
        del frames[10]["wrist_pitch"]
    elif failure == "nan":
        frames[10]["base_pitch"] = float("nan")
    elif failure == "unsafe_start":
        hal._recovery_raw_start["2"] = 4090
    elif failure == "edge_target":
        frames[-1]["base_pitch"] = hal._raw_to_degrees(2799, hal._calibration["base_pitch"])
    hal._submit_control = Mock()
    with pytest.raises(ValueError):
        hal.prepare_recovery(frames)
    hal._submit_control.assert_not_called()


def test_raw_roundtrip_does_not_shift_recovery_start_one_tick(tmp_path):
    hal = make_hal(tmp_path)
    for cal in hal._calibration.values():
        for raw in (179, 1203, 2047, 2592, 2666, 3222):
            assert hal._degrees_to_raw(hal._raw_to_degrees(raw, cal), cal) == raw


def test_wireless_calibration_centers_and_preserves_encoder_coordinates():
    motors = DeviceConfig().motors
    neutral = {str(i): 2100 for i in range(1, 6)}
    offsets = {str(i): 837 for i in range(1, 6)}
    samples = [{str(i): value for i in range(1, 6)} for value in range(1700, 2501, 50)]
    result = calibration_preview(neutral, offsets, samples, motors)
    joint = result["base_pitch"]
    assert joint["homing_offset"] == 890
    assert joint["range_min"] == 1647 and joint["range_max"] == 2447
    assert 2100 + 837 - joint["homing_offset"] == joint["neutral_raw"] == 2047


def test_calibration_refuses_unmoved_joint():
    raw = {str(i): 2047 for i in range(1, 6)}
    with pytest.raises(ValueError, match="记录范围"):
        calibration_preview(raw, {str(i): 0 for i in range(1, 6)}, [raw] * 10, DeviceConfig().motors)


def test_unsolicited_p4_frame_blocks_usb_without_transmitting():
    serial = Mock(timeout=0.1, in_waiting=8)
    serial.read.return_value = bytes.fromhex("ff ff 02 04 02 38 0f b0")
    with pytest.raises(RuntimeError, match="未发送命令"):
        require_quiet_serial(serial)
    serial.write.assert_not_called()
    assert serial.timeout == 0.1


def test_scan_rejects_echo_foreign_read_and_corrupt_checksum():
    ping = bytes.fromhex("ff ff 02 02 01 fa")
    reply = bytes.fromhex("ff ff 02 02 00 fb")
    assert not has_ping_reply(ping, 2, ping)
    assert not has_ping_reply(bytes.fromhex("ff ff 02 04 02 38 0f b0"), 2, ping)
    assert not has_ping_reply(reply[:-1] + b"\x00", 2, ping)
    assert has_ping_reply(ping + reply, 2, ping)


def test_diagnostic_redaction_and_bundle_contains_no_credential_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    (tmp_path / "credentials.json").write_text('{"api_key":"never-include"}')
    data = redact(
        {
            "api_key": "secret",
            "auth_proof": "proof",
            "messages": ["private"],
            "error": "Bearer abc123 api_key=hidden",
            "status": {"id": 2, "position": 2666},
        }
    )
    assert "abc123" not in str(data) and "hidden" not in str(data)
    assert data["status"]["position"] == 2666
    server = SimpleNamespace(
        hal=SimpleNamespace(transport_status=lambda: data),
        maintenance=SimpleNamespace(status=lambda: {}),
        config=SimpleNamespace(device=DeviceConfig()),
    )
    import zipfile

    with zipfile.ZipFile(support_bundle(server)) as archive:
        assert "credentials.json" not in archive.namelist()
        assert "never-include" not in archive.read("status.json").decode()


def maintenance_fixture(tmp_path):
    hal = make_hal(tmp_path)
    hal._device_snapshot["maintenance_version"] = 2
    raw = {str(i): 2100 for i in range(1, 6)}
    response = {
        "ok": True,
        "positions": raw,
        "online_ids": [1, 2, 3, 4, 5],
        "maintenance": True,
        "torque_enabled": False,
        "metrics": {str(i): {"homing_offset": 0} for i in range(1, 6)},
    }
    hal.enter_maintenance = Mock(return_value=response)
    hal.maintenance_snapshot = Mock(return_value=response)
    server = SimpleNamespace(
        hal=hal,
        config=SimpleNamespace(device=hal._config),
        _record_recorder=None,
        _record_lock=asyncio.Lock(),
        lighting_mode=SimpleNamespace(is_engaged=False),
        executor=SimpleNamespace(set_motion_block_reason=Mock(), cancel_current=AsyncMock()),
        motion=SimpleNamespace(stop=Mock(), start=Mock()),
    )
    service = P4Maintenance(server)
    return service, hal, raw


def test_calibration_ack_precedes_atomic_file_commit_and_preserves_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))

    async def run():
        service, hal, raw = maintenance_fixture(tmp_path)
        original = (tmp_path / "T.json").read_bytes()
        began = await service.command("begin", {"confirmed": True})
        service.neutral = raw
        service.offsets = {k: 0 for k in raw}
        samples = [{str(i): value for i in range(1, 6)} for value in range(1700, 2501, 50)]
        service.preview = calibration_preview(raw, service.offsets, samples, hal._config.motors)
        service.phase = "review"

        def device_ack(op, body, **kwargs):
            assert op == "calibration_apply"
            assert (tmp_path / "T.json").read_bytes() == original
            assert list(tmp_path.glob("T.pending-*.json"))
            return {"ok": True, "profile_sha256": body["profile"]["profile_sha256"]}

        hal._submit_control = Mock(side_effect=device_ack)
        result = await service.command("commit", {"session": began["session"], "confirmed": True})
        assert result["phase"] == "saved"
        assert Path(result["backup"]).read_bytes() == original
        assert json.loads((tmp_path / "T.json").read_text())["base_pitch"]["homing_offset"] == 53
        assert not list(tmp_path.glob("T.pending-*.json"))

    from pathlib import Path

    asyncio.run(run())


def test_uncertain_commit_keeps_original_and_pending_and_blocks_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))

    async def run():
        service, hal, raw = maintenance_fixture(tmp_path)
        original = (tmp_path / "T.json").read_bytes()
        began = await service.command("begin", {"confirmed": True})
        service.neutral = raw
        service.preview = calibration_preview(
            raw, {k: 0 for k in raw}, [{k: value for k in raw} for value in range(1700, 2501, 50)], hal._config.motors
        )
        service.phase = "review"
        hal._submit_control = Mock(side_effect=RuntimeError("lost acknowledgement"))
        with pytest.raises(RuntimeError, match="lost acknowledgement"):
            await service.command("commit", {"session": began["session"], "confirmed": True})
        assert (tmp_path / "T.json").read_bytes() == original
        assert list(tmp_path.glob("T.pending-*.json"))
        with pytest.raises(RuntimeError, match="不确定"):
            await service.command("finish", {"session": began["session"], "confirmed": True})
        service.server.motion.start.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["not_written", "rolled_back", "uncertain"])
def test_rejected_save_uses_device_readback_outcome(tmp_path, monkeypatch, outcome):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))

    async def run():
        service, hal, raw = maintenance_fixture(tmp_path)
        original = (tmp_path / "T.json").read_bytes()
        began = await service.command("begin", {"confirmed": True})
        service.neutral = raw
        service.preview = calibration_preview(
            raw, {k: 0 for k in raw}, [{k: value for k in raw} for value in range(1700, 2501, 50)], hal._config.motors
        )
        service.phase = "review"
        hal._submit_control = Mock(side_effect=P4ControlError("write failed", {
            "ok": False, "calibration_result": outcome,
            "calibration_error": "servo 1 register 31: wanted 1043, read 827",
            "rollback_error": "servo 2 rollback offset=900" if outcome == "uncertain" else "",
        }))
        with pytest.raises(RuntimeError, match="register 31"):
            await service.command("commit", {"session": began["session"], "confirmed": True})
        assert service.phase == ("commit_uncertain" if outcome == "uncertain" else "review")
        assert "register 31" in service.status()["error"]
        assert (tmp_path / "T.json").read_bytes() == original
        assert list(tmp_path.glob("T.pending-*.json"))
        service.server.motion.start.assert_not_called()

    asyncio.run(run())


def test_duplicate_and_wrong_browser_session_cannot_start_calibration(tmp_path):
    async def run():
        service, hal, _ = maintenance_fixture(tmp_path)
        await service.command("begin", {"confirmed": True})
        with pytest.raises(RuntimeError, match="已有"):
            await service.command("begin", {"confirmed": True})
        with pytest.raises(RuntimeError, match="失效"):
            await service.command("neutral", {"session": "other-tab"})
        hal.enter_maintenance.assert_called_once()

    asyncio.run(run())


def test_calibration_can_save_far_from_recorded_neutral_without_redefining_it(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))

    async def run():
        service, hal, raw = maintenance_fixture(tmp_path)
        began = await service.command("begin", {"confirmed": True})
        service.neutral, service.offsets = raw, {k: 0 for k in raw}
        service.preview = calibration_preview(
            raw, service.offsets, [{k: value for k in raw} for value in range(1700, 2501, 50)], hal._config.motors
        )
        service.phase = "review"
        hal.maintenance_snapshot.return_value["positions"] = {k: 2850 for k in raw}

        def acknowledge(op, payload, **kwargs):
            assert op == "calibration_apply"
            for joint in payload["profile"]["joints"]:
                assert joint["homing_offset"] == 53
                assert joint["neutral_raw"] == 2047
            return {"ok": True, "profile_sha256": payload["profile"]["profile_sha256"]}

        hal._submit_control = Mock(side_effect=acknowledge)
        result = await service.command("commit", {"session": began["session"], "confirmed": True})
        assert result["phase"] == "saved"

    asyncio.run(run())


def test_old_firmware_cannot_attempt_pose_independent_calibration(tmp_path):
    async def run():
        service, hal, raw = maintenance_fixture(tmp_path)
        began = await service.command("begin", {"confirmed": True})
        service.preview = {"valid": True}
        service.phase = "review"
        hal._device_snapshot["maintenance_version"] = 1
        hal.apply_calibration_file = Mock()
        with pytest.raises(RuntimeError, match="升级 P4"):
            await service.command("commit", {"session": began["session"], "confirmed": True})
        hal.apply_calibration_file.assert_not_called()

    asyncio.run(run())


def test_valid_preview_survives_restart_and_rejects_changed_base(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))

    async def run():
        service, hal, raw = maintenance_fixture(tmp_path)
        service.neutral, service.offsets = raw, {k: 0 for k in raw}
        service.samples = [{k: value for k in raw} for value in range(1700, 2501, 50)]
        service.preview = calibration_preview(raw, service.offsets, service.samples, hal._config.motors)
        service._save_draft()
        restarted = P4Maintenance(service.server)
        assert restarted.status()["draft_available"]
        result = await restarted.command("begin", {"confirmed": True})
        assert result["phase"] == "review"
        assert result["samples"] == len(service.samples)
        assert result["preview"] == service.preview and result["neutral"] == raw
        with (tmp_path / "T.json").open("a") as stream:
            stream.write("\n")
        assert restarted._load_draft() is None

    asyncio.run(run())


def test_id2_preview_is_a_coordinate_shift_not_a_reduced_range():
    neutral = {str(i): 1884 for i in range(1, 6)}
    offsets = {str(i): 837 for i in range(1, 6)}
    samples = [{str(i): value for i in range(1, 6)} for value in [1114, 2093] * 5]
    joint = calibration_preview(neutral, offsets, samples, DeviceConfig().motors)["base_pitch"]
    assert joint["homing_offset"] == 674
    assert (joint["range_min"], joint["range_max"]) == (1277, 2256)
    assert joint["range_max"] - joint["range_min"] == 2093 - 1114


def test_restart_with_saved_preview_does_not_enable_torque_or_home(tmp_path, monkeypatch):
    from lampgo.core.config import LampgoConfig
    from lampgo.server import LampgoServer

    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))
    make_hal(tmp_path)
    server = LampgoServer(
        LampgoConfig(
            home_on_start=True,
            device=DeviceConfig(motor_transport="p4", calibration_dir=tmp_path, lamp_id="T"),
        )
    )
    server.maintenance._load_draft = Mock(return_value={"preview": {}})
    server.hal = Mock(startup_state=MotorStartupState.READY)
    server.hal.get_calibration_home.return_value = None
    server.motion = Mock()
    server.esp32.start = AsyncMock()
    server.led.connect = Mock()
    server._register_builtin_skills = Mock()
    server._load_user_skills = Mock()
    server._home_on_start = AsyncMock()
    server._start_idle_sway_scheduler = Mock()
    server._ipc.start = AsyncMock()
    server._setup_llm_router = Mock()
    asyncio.run(server.start())
    server.hal.connect.assert_called_once_with(configure=False)
    server.motion.start.assert_not_called()
    server._home_on_start.assert_not_called()


def test_motion_runtime_streams_p4_recovery_to_verified_completion(tmp_path):
    """Exercise the real planner/HAL boundary against a simulated servo executor."""
    from lampgo.core.config import MotionConfig, SafetyConfig
    from lampgo.core.motion import MotionRuntime
    from lampgo.core.safety import SafetyKernel

    hal = make_hal(tmp_path)
    raw = {str(i): 3000 if i == 2 else 2047 for i in range(1, 6)}
    previous = dict(raw)
    target = {}
    errors = []

    def feedback():
        packet = {"positions": dict(raw), "online_ids": [1, 2, 3, 4, 5], "startup_state": hal.startup_state.value}
        hal._apply_telemetry(packet)
        return packet

    def control(op, payload, **kwargs):
        if op == "recovery_prepare":
            target.update(payload["targets"])
            hal._startup_state = MotorStartupState.RECOVERING
        elif op == "recovery_finish":
            assert all(abs(raw[k] - target[k]) <= 58 for k in target)
            hal._startup_state = MotorStartupState.READY
        return {"ok": True, **feedback()}

    read = hal.read_positions

    def simulated_read():
        with hal._latest_lock:
            frame, hal._latest_frame = hal._latest_frame, None
        if frame and frame.get("recovery"):
            for key, goal in frame["targets"].items():
                if not min(previous[key], target[key]) <= goal <= max(previous[key], target[key]):
                    errors.append((key, previous[key], goal))
                raw[key] = previous[key] + max(-8, min(8, goal - previous[key]))
                previous[key] = raw[key]
        feedback()
        return read()

    hal._submit_control = Mock(side_effect=control)
    hal.read_positions = simulated_read
    motion = MotionRuntime(hal, SafetyKernel(SafetyConfig()), MotionConfig(breathing_enabled=False))
    safe = {name: hal._raw_to_degrees(2047, cal) for name, cal in hal._calibration.items()}
    try:
        frames = motion.prepare_recovery(safe, max_velocity=30, fps=50)
        done = motion.stream_recovery_frames(frames, fps=50)
        assert done.wait(6)
        assert not motion.recovery_error
        motion.complete_recovery()
        assert hal.startup_state is MotorStartupState.READY
        assert not errors
    finally:
        motion.stop()


def test_p4_recording_stop_then_discard_preserves_hold_without_second_exit(tmp_path):
    from lampgo.server import LampgoServer

    hal = make_hal(tmp_path)
    hal._maintenance = True
    hal._startup_state = MotorStartupState.READY
    server = SimpleNamespace(hal=hal, executor=Mock())

    def control(op, payload, **kwargs):
        if op == "maintenance_exit":
            assert hal._maintenance
        return {"ok": True, "startup_state": "ready", "torque_enabled": op == "torque"}

    hal._submit_control = Mock(side_effect=control)

    async def run():
        await LampgoServer._finish_teach_motion(server)
        await LampgoServer._finish_teach_motion(server)

    asyncio.run(run())
    assert [call.args[0] for call in hal._submit_control.call_args_list] == ["maintenance_exit", "torque"]
