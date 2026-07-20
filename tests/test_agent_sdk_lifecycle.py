from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from lampgo.core.config import VoiceConfig
from lampgo.voice import agent_sdk


def _manager() -> agent_sdk.AgentSDKManager:
    return agent_sdk.AgentSDKManager(VoiceConfig())


def test_command_runs_agent_sdk_matches_exact_executable() -> None:
    assert agent_sdk._command_runs_agent_sdk(
        ["python3", "/tmp/.venv/bin/lampgo-livekit-agent", "--port", "18790"]
    )
    assert agent_sdk._command_runs_agent_sdk([r"C:\venv\lampgo-livekit-agent.exe"])
    assert not agent_sdk._command_runs_agent_sdk(["python3", "-m", "lampgo.cli"])
    assert not agent_sdk._command_runs_agent_sdk(["lampgo-livekit-agent-helper"])


def test_find_port_listener_falls_back_to_current_user_processes(monkeypatch) -> None:
    manager = _manager()

    class AccessDenied(Exception):
        pass

    class NoSuchProcess(Exception):
        pass

    process = SimpleNamespace(
        pid=2468,
        net_connections=lambda *, kind: [
            SimpleNamespace(
                status="LISTEN",
                laddr=SimpleNamespace(port=manager.port),
            )
        ],
    )
    fake_psutil = SimpleNamespace(
        AccessDenied=AccessDenied,
        NoSuchProcess=NoSuchProcess,
        CONN_LISTEN="LISTEN",
        net_connections=lambda *, kind: (_ for _ in ()).throw(AccessDenied()),
        process_iter=lambda: [process],
    )
    monkeypatch.setattr(manager, "_psutil", lambda: fake_psutil)
    monkeypatch.setattr(manager, "_process_is_current_user", lambda _process: True)

    assert manager._find_port_listener_pid() == process.pid


@pytest.mark.asyncio
async def test_release_sdk_port_does_nothing_when_port_is_free(monkeypatch) -> None:
    manager = _manager()
    monkeypatch.setattr(manager, "_find_port_listener_pid", lambda: None)

    assert await manager._release_sdk_port()


@pytest.mark.asyncio
async def test_release_sdk_port_refuses_unknown_listener(monkeypatch) -> None:
    manager = _manager()
    monkeypatch.setattr(manager, "_find_port_listener_pid", lambda: 4321)
    monkeypatch.setattr(manager, "_identify_sdk_port_owner", lambda _pid: None)
    monkeypatch.setattr(manager, "_describe_process", lambda _pid: "other-server")

    assert not await manager._release_sdk_port()
    assert "unknown process" in manager.last_error
    assert "4321" in manager.last_error


@pytest.mark.asyncio
async def test_release_sdk_port_terminates_verified_process_group(monkeypatch) -> None:
    manager = _manager()
    owner = agent_sdk._SDKPortOwner(
        listener_pid=17368,
        root_pid=17356,
        process_group_id=17356,
        process_name="python3.12",
    )
    signals: list[bool] = []
    waits = iter([False, True])

    monkeypatch.setattr(manager, "_find_port_listener_pid", lambda: owner.listener_pid)
    monkeypatch.setattr(manager, "_identify_sdk_port_owner", lambda _pid: owner)
    monkeypatch.setattr(manager, "_signal_sdk_owner", lambda _owner, *, force: signals.append(force))

    async def fake_wait(_timeout_s: float) -> bool:
        await asyncio.sleep(0)
        return next(waits)

    monkeypatch.setattr(manager, "_wait_for_port_free", fake_wait)

    assert await manager._release_sdk_port()
    assert signals == [False, True]


@pytest.mark.asyncio
async def test_wait_ready_returns_immediately_on_bind_failure() -> None:
    manager = _manager()
    manager._process = type("Process", (), {"returncode": None})()
    manager._set_last_error("TCP port 18790 became unavailable during SDK startup")
    manager._startup_failed_event.set()

    assert not await manager.wait_ready(timeout_s=10.0)


@pytest.mark.asyncio
async def test_monitor_turns_bind_error_into_startup_failure() -> None:
    manager = _manager()

    class Process:
        returncode = 1

        def __init__(self) -> None:
            self.stdout = self._lines()

        @staticmethod
        async def _lines():
            yield b"ERROR: [Errno 48] address already in use\n"

        async def wait(self) -> int:
            return self.returncode

    process = Process()
    manager._process = process

    await manager._monitor()

    assert manager._startup_failed_event.is_set()
    assert "18790" in manager.last_error
    assert not manager.is_running
