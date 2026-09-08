from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from lampgo.core.config import LLMConfig, VoiceConfig
from lampgo.voice import agent_sdk


def _manager() -> agent_sdk.AgentSDKManager:
    return agent_sdk.AgentSDKManager(VoiceConfig(), _mimo_llm())


def _mimo_llm() -> LLMConfig:
    return LLMConfig(
        provider="mimo",
        api_base="https://api.xiaomimimo.com/v1",
        api_key="mimo-key",
    )


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


def test_windows_process_tree_is_killed_after_graceful_timeout(monkeypatch) -> None:
    manager = _manager()
    events: list[str] = []

    class NoSuchProcess(Exception):
        pass

    class FakeProcess:
        def __init__(self, pid: int, children=None) -> None:
            self.pid = pid
            self._children = list(children or [])

        def children(self, *, recursive: bool):
            assert recursive
            return list(self._children)

        def terminate(self) -> None:
            events.append(f"terminate:{self.pid}")

        def kill(self) -> None:
            events.append(f"kill:{self.pid}")

    child = FakeProcess(2002)
    root = FakeProcess(2001, [child])
    waits = iter(
        [
            ([], [child]),
            ([child], []),
        ]
    )
    fake_psutil = SimpleNamespace(
        NoSuchProcess=NoSuchProcess,
        Process=lambda pid: root if pid == root.pid else (_ for _ in ()).throw(NoSuchProcess()),
        wait_procs=lambda processes, timeout: next(waits),
    )
    monkeypatch.setattr(manager, "_psutil", lambda: fake_psutil)

    assert manager._stop_windows_process_tree(root.pid, timeout_s=0.01) == (True, [])
    assert events == ["terminate:2002", "terminate:2001", "kill:2002"]


def test_windows_process_tree_reports_force_kill_failure(monkeypatch) -> None:
    manager = _manager()

    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    child = SimpleNamespace(
        pid=2002,
        terminate=lambda: None,
        kill=lambda: None,
    )
    root = SimpleNamespace(
        pid=2001,
        children=lambda *, recursive: [child],
        terminate=lambda: None,
        kill=lambda: None,
    )
    waits = iter([
        ([], [child]),
        ([], [child]),
    ])
    fake_psutil = SimpleNamespace(
        NoSuchProcess=NoSuchProcess,
        AccessDenied=AccessDenied,
        Process=lambda _pid: root,
        wait_procs=lambda processes, timeout: next(waits),
    )
    monkeypatch.setattr(manager, "_psutil", lambda: fake_psutil)

    assert manager._stop_windows_process_tree(root.pid, timeout_s=0.01) == (True, [child.pid])


def test_windows_process_tree_tolerates_access_denied(monkeypatch) -> None:
    manager = _manager()

    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    child = SimpleNamespace(
        pid=2002,
        terminate=lambda: (_ for _ in ()).throw(AccessDenied()),
        kill=lambda: (_ for _ in ()).throw(AccessDenied()),
    )
    root = SimpleNamespace(
        pid=2001,
        children=lambda *, recursive: [child],
        terminate=lambda: None,
        kill=lambda: None,
    )
    waits = iter([
        ([root], [child]),
        ([], [child]),
    ])
    fake_psutil = SimpleNamespace(
        NoSuchProcess=NoSuchProcess,
        AccessDenied=AccessDenied,
        Process=lambda _pid: root,
        wait_procs=lambda processes, timeout: next(waits),
    )
    monkeypatch.setattr(manager, "_psutil", lambda: fake_psutil)

    assert manager._stop_windows_process_tree(root.pid, timeout_s=0.01) == (True, [child.pid])


@pytest.mark.asyncio
async def test_stop_uses_windows_process_tree(monkeypatch) -> None:
    manager = _manager()
    calls: list[int] = []

    class Process:
        pid = 2468
        returncode = None

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    manager._process = Process()
    monkeypatch.setattr(agent_sdk.os, "name", "nt")

    def fake_stop_process_tree(pid: int):
        calls.append(pid)
        return False, []

    monkeypatch.setattr(manager, "_stop_windows_process_tree", fake_stop_process_tree)

    await manager.stop()

    assert calls == [2468]
    assert manager._process is None


@pytest.mark.asyncio
async def test_stop_reaps_root_when_process_tree_cleanup_errors(monkeypatch) -> None:
    manager = _manager()
    waits: list[int] = []

    class Process:
        pid = 2468
        returncode = None

        async def wait(self) -> int:
            waits.append(self.pid)
            self.returncode = 0
            return 0

    manager._process = Process()
    monkeypatch.setattr(agent_sdk.os, "name", "nt")
    monkeypatch.setattr(
        manager,
        "_stop_windows_process_tree",
        lambda _pid: (_ for _ in ()).throw(RuntimeError("inspection failed")),
    )

    await manager.stop()

    assert waits == [2468]
    assert manager._process is None


@pytest.mark.asyncio
async def test_stop_reports_remaining_child_processes(monkeypatch) -> None:
    manager = _manager()

    class Process:
        pid = 2468
        returncode = None

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    manager._process = Process()
    monkeypatch.setattr(agent_sdk.os, "name", "nt")
    monkeypatch.setattr(
        manager,
        "_stop_windows_process_tree",
        lambda _pid: (True, [2469]),
    )

    await manager.stop()

    assert "2469" in manager.last_error


@pytest.mark.asyncio
async def test_stop_reports_root_process_not_confirmed(monkeypatch) -> None:
    manager = _manager()

    class Process:
        pid = 2468
        returncode = None

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    manager._process = Process()
    monkeypatch.setattr(agent_sdk.os, "name", "nt")
    monkeypatch.setattr(
        manager,
        "_stop_windows_process_tree",
        lambda _pid: (False, [2468]),
    )

    await manager.stop()

    assert "root process" in manager.last_error
    assert "2468" in manager.last_error


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
