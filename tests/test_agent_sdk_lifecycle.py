from __future__ import annotations

import asyncio
import socket
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


@pytest.mark.parametrize("system_error", ["access_denied", "runtime_error", "no_such_process"])
@pytest.mark.parametrize("process_error", [False, True])
def test_find_port_listener_falls_back_to_current_user_processes(
    monkeypatch, system_error, process_error,
) -> None:
    manager = _manager()

    class AccessDenied(Exception):
        pass

    class NoSuchProcess(Exception):
        pass

    def fail_process_query(*, kind):
        raise RuntimeError("proc_pidinfo(PROC_PIDLISTFDS) 2/2 syscall failed")

    def fail_system_query(*, kind):
        error_class = {
            "access_denied": AccessDenied,
            "runtime_error": RuntimeError,
            "no_such_process": NoSuchProcess,
        }[system_error]
        raise error_class("proc_pidinfo(PROC_PIDLISTFDS) 2/2 syscall failed")

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
        net_connections=fail_system_query,
        process_iter=lambda: (
            [SimpleNamespace(pid=1234, net_connections=fail_process_query), process]
            if process_error else [process]
        ),
    )
    monkeypatch.setattr(manager, "_psutil", lambda: fake_psutil)
    monkeypatch.setattr(manager, "_process_is_current_user", lambda _process: True)

    assert manager._find_port_listener_pid() == process.pid


@pytest.mark.asyncio
@pytest.mark.parametrize("occupied", [False, True])
async def test_failed_process_queries_still_check_actual_port(monkeypatch, occupied) -> None:
    """A failed scan is neither proof of a free port nor permission to kill."""
    import psutil

    manager = _manager()

    def fail_query(*, kind):
        raise RuntimeError("proc_pidinfo(PROC_PIDLISTFDS) 2/2 syscall failed")

    monkeypatch.setattr(psutil, "net_connections", fail_query)
    monkeypatch.setattr(psutil, "process_iter", lambda: [
        SimpleNamespace(pid=1234, net_connections=fail_query),
    ])
    monkeypatch.setattr(manager, "_process_is_current_user", lambda _process: True)

    def unexpected_signal(*args, **kwargs):
        pytest.fail("An uninspectable listener must never be terminated")

    monkeypatch.setattr(manager, "_signal_sdk_owner", unexpected_signal)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        manager._port = listener.getsockname()[1]
        listener.listen()
        if not occupied:
            listener.close()
        assert await manager._release_sdk_port() is (not occupied)
        if occupied:
            assert "occupied but its owner cannot be inspected" in manager.last_error


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


def test_sdk_preserves_system_proxy_before_adding_local_bypass(monkeypatch):
    monkeypatch.setattr(agent_sdk.sys, "platform", "darwin")
    monkeypatch.setattr(agent_sdk, "getproxies", lambda: {
        "http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890",
        "socks": "socks5://127.0.0.1:7890",
    })
    env = {"NO_PROXY": "example.internal"}
    agent_sdk.AgentSDKManager._configure_network_env(env)
    assert env["HTTP_PROXY"] == env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert env["NO_PROXY"] == env["no_proxy"]
    assert "example.internal" in env["NO_PROXY"].split(",")
    assert "127.0.0.1" in env["NO_PROXY"].split(",")
    assert "ALL_PROXY" not in env
    # httpx must actually select direct routing for the backend, proxy for RTC.
    from httpx._utils import get_environment_proxies
    from unittest.mock import patch
    with patch.dict("os.environ", env, clear=True):
        routes = get_environment_proxies()
    assert routes["https://"] == "http://127.0.0.1:7890"
    assert routes["all://127.0.0.1"] is None


@pytest.mark.parametrize("env", [
    {"HTTPS_PROXY": "http://explicit.test:8888"},
    {"HTTP_PROXY": ""},
    {"ALL_PROXY": "socks5://explicit.test:1080"},
    {"https_proxy": "http://explicit.test:8888"},
])
def test_sdk_respects_explicit_proxy_and_opt_out(monkeypatch, env):
    monkeypatch.setattr(agent_sdk.sys, "platform", "darwin")
    def unexpected():
        raise AssertionError("explicit proxy choice must not be replaced")
    monkeypatch.setattr(agent_sdk, "getproxies", unexpected)
    original = dict(env)
    agent_sdk.AgentSDKManager._configure_network_env(env)
    for key, value in original.items():
        assert env[key] == value
    if "https_proxy" in env:
        assert env["HTTPS_PROXY"] == env["https_proxy"]


def test_sdk_no_system_proxy_keeps_direct_route(monkeypatch):
    monkeypatch.setattr(agent_sdk.sys, "platform", "darwin")
    monkeypatch.setattr(agent_sdk, "getproxies", lambda: {})
    env = {}
    agent_sdk.AgentSDKManager._configure_network_env(env)
    assert set(env) == {"NO_PROXY", "no_proxy"}
