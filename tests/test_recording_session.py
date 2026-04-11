"""Run-mode recording session tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.server import LampgoServer
from tests.conftest import MockHAL


def _make_server_with_mock_hal(tmp_path) -> tuple[LampgoServer, MockHAL]:
    server = LampgoServer(
        LampgoConfig(
            device=DeviceConfig(motor_port="/dev/null"),
            recordings_dir=tmp_path,
        )
    )
    hal = MockHAL()
    hal.connect()
    server.hal = hal
    server.motion = SimpleNamespace(
        stop=lambda: None,
        start=lambda: None,
        is_running=True,
        current_state=hal.read_positions(),
    )

    async def _noop_cancel_current() -> None:
        return None

    server.executor.cancel_current = _noop_cancel_current
    return server, hal


@pytest.mark.asyncio
async def test_recording_session_start_stop_save(tmp_path):
    server, hal = _make_server_with_mock_hal(tmp_path)

    started = await server.start_recording_session(fps=30)
    assert started["ok"] is True
    assert hal.torque_disabled is True

    await asyncio.sleep(0.08)
    stopped = await server.stop_recording_session()
    assert stopped["ok"] is True
    assert stopped["result"]["frames"] > 0
    assert hal.torque_enabled is True

    saved = await server.save_recording_session("my_action")
    assert saved["ok"] is True
    assert saved["result"]["name"] == "my_action"
    assert (tmp_path / "user" / "my_action.csv").exists()


@pytest.mark.asyncio
async def test_recording_blocks_invoke_until_saved_or_discarded(tmp_path):
    server, _ = _make_server_with_mock_hal(tmp_path)

    await server.start_recording_session(fps=20)
    blocked_active = await server.handle_request({"cmd": "invoke", "skill_id": "dance", "params": {}})
    assert blocked_active["ok"] is False
    assert "recording session active" in blocked_active["error"]

    await asyncio.sleep(0.04)
    await server.stop_recording_session()
    blocked_pending = await server.handle_request({"cmd": "invoke", "skill_id": "dance", "params": {}})
    assert blocked_pending["ok"] is False

    discarded = await server.discard_recording_session()
    assert discarded["ok"] is True


@pytest.mark.asyncio
async def test_status_contains_recording_fields(tmp_path):
    server, _ = _make_server_with_mock_hal(tmp_path)

    status0 = await server.handle_request({"cmd": "status"})
    rec0 = status0["result"]["recording"]
    assert rec0["active"] is False
    assert rec0["has_buffer"] is False

    await server.start_recording_session(fps=25)
    await asyncio.sleep(0.05)
    status1 = await server.handle_request({"cmd": "status"})
    rec1 = status1["result"]["recording"]
    assert rec1["active"] is True
    assert rec1["frames"] > 0

    await server.stop_recording_session()
    status2 = await server.handle_request({"cmd": "status"})
    rec2 = status2["result"]["recording"]
    assert rec2["active"] is False
    assert rec2["has_buffer"] is True


@pytest.mark.asyncio
async def test_recording_save_name_conflict_requires_overwrite(tmp_path):
    server, _ = _make_server_with_mock_hal(tmp_path)

    await server.start_recording_session(fps=20)
    await asyncio.sleep(0.04)
    await server.stop_recording_session()
    first = await server.save_recording_session("dup_name")
    assert first["ok"] is True

    await server.start_recording_session(fps=20)
    await asyncio.sleep(0.04)
    await server.stop_recording_session()
    conflict = await server.save_recording_session("dup_name")
    assert conflict["ok"] is False
    assert conflict["result"]["status"] == "name_conflict"
    assert conflict["result"]["requires_overwrite"] is True

    forced = await server.save_recording_session("dup_name", overwrite=True)
    assert forced["ok"] is True
