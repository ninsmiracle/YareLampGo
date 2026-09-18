"""Delayed mDNS discovery must be recoverable without automatic motion."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from lampgo.core.config import LampgoConfig
from lampgo.core.hal import MotorStartupState
from lampgo.core.types import InvokeResult, JointState
from lampgo.server import LampgoServer


@pytest.fixture
def server(monkeypatch, tmp_path):
    config = LampgoConfig(no_hw=True, home_on_start=False)
    config.device.motor_transport = "p4"
    config.device.calibration_dir = tmp_path
    server = LampgoServer(config)
    server._started = True
    server.maintenance._load_draft = Mock(return_value=None)
    server._hal_startup_error = "P4 motion connection failed: no ESP32-P4 host discovered"
    server.executor.set_motion_block_reason(server._hal_startup_error)
    server.esp32 = Mock()
    server.esp32.get_active_host.return_value = "192.0.2.4"
    server.esp32.start = AsyncMock()
    server.hal = Mock()
    server.motion = Mock(is_running=False, current_state=JointState(positions={}))
    server.lighting_mode = Mock(is_engaged=False)
    server.executor.invoke = AsyncMock(return_value=InvokeResult(invocation_id="test", status="ok"))
    new_motion = Mock(is_running=False, current_state=JointState(positions={}))
    monkeypatch.setattr("lampgo.server.MotionRuntime", Mock(return_value=new_motion))
    return server


def new_hal(state=MotorStartupState.READY):
    hal = Mock(is_connected=True, startup_state=state,
               recovery_required=state is MotorStartupState.RECOVERY_REQUIRED,
               supports_remote_recovery=True, recovery_reason="pose needs recovery")
    hal.get_calibration_home.return_value = None
    return hal


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [MotorStartupState.READY, MotorStartupState.RECOVERY_REQUIRED,
                                    MotorStartupState.HARD_FAULT])
async def test_safe_return_reconnects_late_device_and_replaces_stale_context(server, state):
    hal = new_hal(state)
    server._new_motor_hal = Mock(return_value=hal)
    old_context = server.make_context()
    await server._invoke_lampgo_skill("return_safe", old_context, reason="invoke:return_safe")
    hal.connect.assert_called_once()
    assert not server.config.no_hw
    assert server._hal_startup_error is None
    assert server.executor.invoke.call_args.args[1].motion is server.motion
    assert server.motion is not old_context.motion
    if state is MotorStartupState.READY:
        server.motion.start.assert_called_once()
        assert server.executor._motion_block_reason is None
    else:
        server.motion.start.assert_not_called()
        assert server.executor._motion_block_reason
        assert server.executor._allow_return_safe_recovery == (state is MotorStartupState.RECOVERY_REQUIRED)


@pytest.mark.asyncio
async def test_parallel_recovery_does_not_replace_recovered_hal_twice(server):
    hal = new_hal()
    server._new_motor_hal = Mock(return_value=hal)
    await asyncio.gather(*(server.reload_motor_runtime(only_if_startup_failed=True) for _ in range(2)))
    hal.connect.assert_called_once()
    server._new_motor_hal.assert_called_once()


@pytest.mark.asyncio
async def test_failed_retry_remains_blocked_and_can_retry_again(server):
    hal = new_hal()
    hal.connect.side_effect = RuntimeError("motion port unavailable")
    server._new_motor_hal = Mock(return_value=hal)
    server._use_virtual_motion = Mock()
    result = await server.reload_motor_runtime(only_if_startup_failed=True)
    assert not result["ok"]
    assert server.config.no_hw
    assert server._hal_startup_error == "motion port unavailable"
    assert server.executor._motion_block_reason == "motion port unavailable"
    hal.connect.side_effect = None
    result = await server.reload_motor_runtime(only_if_startup_failed=True)
    assert result["ok"]
    assert server._hal_startup_error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["simulation", "no_host", "serial", "expression", "gesture", "maintenance", "draft"])
async def test_retry_does_not_bypass_other_modes(server, case):
    skill = "return_safe"
    if case == "simulation":
        server._hal_startup_error = None
    elif case == "no_host":
        server.esp32.get_active_host.return_value = None
    elif case == "serial":
        server.config.device.motor_transport = "serial"
    elif case == "expression":
        skill = "set_expression"
    elif case == "gesture":
        skill = "conversation_gesture"
    elif case == "maintenance":
        server.maintenance = Mock(active=True)
    elif case == "draft":
        server.maintenance._load_draft.return_value = {"preview": True}
    server._new_motor_hal = Mock()
    await server._invoke_lampgo_skill(skill, server.make_context(), reason=f"invoke:{skill}")
    server._new_motor_hal.assert_not_called()
