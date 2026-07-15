from pathlib import Path
from typing import Any

import pytest

from lampgo.agent.codex import CodexRunResult, _entry_matches
from lampgo.agent.indicator import AgentLedIndicator
from lampgo.agent.manager import AgentManager
from lampgo.agent.progress import summarize_codex_event
from lampgo.core.config import LampgoConfig
from lampgo.core.events import AgentTaskUpdated, EventBus
from lampgo.server import LampgoServer


async def _publish_agent_status(events: EventBus, task_id: str, status: str) -> None:
    await events.publish(
        AgentTaskUpdated(
            request_id=f"req-{task_id}",
            task={"task_id": task_id, "status": status},
        )
    )


@pytest.mark.asyncio
async def test_agent_led_indicator_maps_task_lifecycle() -> None:
    events = EventBus()
    modes: list[str] = []
    indicator = AgentLedIndicator(events, lambda mode: modes.append(mode) is None)

    await _publish_agent_status(events, "one", "queued")
    await indicator.flush()
    await _publish_agent_status(events, "one", "running")
    await indicator.flush()
    await _publish_agent_status(events, "one", "completed")
    await indicator.flush()
    await _publish_agent_status(events, "two", "failed")
    await indicator.flush()

    assert modes == ["focused", "check", "cross"]
    await indicator.shutdown()


@pytest.mark.asyncio
async def test_agent_led_indicator_stays_focused_while_any_task_is_active() -> None:
    events = EventBus()
    modes: list[str] = []
    indicator = AgentLedIndicator(events, lambda mode: modes.append(mode) is None)

    await _publish_agent_status(events, "one", "running")
    await indicator.flush()
    await _publish_agent_status(events, "two", "running")
    await _publish_agent_status(events, "two", "completed")
    await indicator.flush()
    assert modes == ["focused"]

    await _publish_agent_status(events, "one", "completed")
    await indicator.flush()
    assert modes == ["focused", "check"]
    await indicator.shutdown()


def test_codex_mcp_registration_requires_exact_stdio_command() -> None:
    expected = {
        "name": "lampgo",
        "enabled": True,
        "transport": {
            "type": "stdio",
            "command": __import__("sys").executable,
            "args": ["-m", "lampgo.cli", "mcp-stdio"],
        },
    }
    assert _entry_matches(expected)
    expected["transport"]["args"] = ["old-command"]
    assert not _entry_matches(expected)


@pytest.mark.asyncio
async def test_agent_manager_streams_and_persists_codex_task(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    events = EventBus()
    snapshots: list[dict[str, Any]] = []
    progress_updates: list[dict[str, Any]] = []

    async def capture(event: AgentTaskUpdated) -> None:
        snapshots.append(event.task)
        if event.progress:
            progress_updates.append(event.progress)

    events.subscribe(AgentTaskUpdated, capture)
    manager = AgentManager(events, api_base="http://127.0.0.1:8420")
    received_prompt = ""

    class FakeProvider:
        async def run(self, **kwargs):
            nonlocal received_prompt
            received_prompt = kwargs["prompt"]
            await kwargs["on_event"]({"type": "thread.started", "thread_id": "thread-1"})
            await kwargs["on_event"]({"type": "turn.started"})
            await kwargs["on_event"](
                {
                    "type": "item.completed",
                    "item": {"id": "msg-1", "type": "agent_message", "text": "我先检查项目结构。"},
                }
            )
            await kwargs["on_event"](
                {
                    "type": "item.started",
                    "item": {"id": "cmd-1", "type": "command_execution", "command": "rg -n TODO lampgo"},
                }
            )
            await kwargs["on_event"](
                {
                    "type": "item.completed",
                    "item": {"id": "cmd-1", "type": "command_execution", "command": "rg -n TODO lampgo"},
                }
            )
            return CodexRunResult(ok=True, exit_code=0, final_message="完成啦", thread_id="thread-1")

        async def cancel(self, task_id: str) -> bool:
            return True

    manager._provider = FakeProvider()  # type: ignore[assignment]
    created = await manager.submit_task(
        {
            "request_id": "req-1",
            "user_text": "分析一下项目",
            "workspace": str(tmp_path),
            "context": {"recent_tool_calls": [{"tool_name": "camera_snap"}]},
        }
    )
    await manager._running[created["task_id"]]

    task = manager.get_task(created["task_id"])
    assert task is not None
    assert task["status"] == "completed"
    assert task["provider_thread_id"] == "thread-1"
    assert task["detail"] == "完成啦"
    assert snapshots[0]["status"] == "queued"
    assert snapshots[-1]["status"] == "completed"
    assert any(item["summary"] == "我先检查项目结构。" for item in progress_updates)
    assert any(item["id"] == "item:cmd-1" and item["state"] == "active" for item in progress_updates)
    assert any(item["id"] == "item:cmd-1" and item["state"] == "done" for item in progress_updates)
    assert task["events"][-1]["summary"] == "已执行：rg -n TODO lampgo"
    assert "camera_snap" in received_prompt
    assert (tmp_path / "lampgo" / "agent_tasks.json").exists()


def test_codex_progress_exposes_commentary_but_not_private_reasoning() -> None:
    commentary = summarize_codex_event(
        {
            "type": "item.completed",
            "item": {"id": "msg", "type": "agent_message", "text": "我先核对本地规则，再读取数据。"},
        }
    )
    assert commentary is not None
    assert commentary["kind"] == "commentary"
    assert commentary["summary"] == "我先核对本地规则，再读取数据。"

    encrypted_reasoning = summarize_codex_event(
        {
            "type": "item.completed",
            "item": {"id": "why", "type": "reasoning", "encrypted_content": "private-payload"},
        }
    )
    assert encrypted_reasoning is None

    explicit_summary = summarize_codex_event(
        {
            "type": "item.completed",
            "item": {"id": "why", "type": "reasoning", "summary": ["先读配置", "再验证状态"]},
        }
    )
    assert explicit_summary is not None
    assert explicit_summary["summary"] == "思路摘要：先读配置 再验证状态"


def test_codex_progress_skips_final_answer_and_redacts_command_secrets() -> None:
    final = summarize_codex_event(
        {
            "type": "item.completed",
            "item": {"id": "final", "type": "agent_message", "phase": "final_answer", "text": "任务完成。"},
        }
    )
    assert final is None

    command = summarize_codex_event(
        {
            "type": "item.started",
            "item": {
                "id": "cmd",
                "type": "command_execution",
                "command": "curl -H 'Authorization: Bearer abcdefghijklmnop' token=super-secret localhost",
            },
        }
    )
    assert command is not None
    assert command["state"] == "active"
    assert "super-secret" not in command["summary"]
    assert "abcdefghijklmnop" not in command["summary"]
    assert "[已隐藏]" in command["summary"]


def test_codex_frontend_has_reconnect_and_polling_fallbacks() -> None:
    source = Path("lampgo/web/static/app.js").read_text(encoding="utf-8")
    assert 'fetch("/api/agent/tasks"' in source
    assert "window.setInterval(() => { void pollCodexTasks(); }, 2000)" in source
    assert "handleEvent(e);" in source
    assert "updateCodexLinkCards(task);" in source
    assert source.index("const sessionId = agentTaskSessions.get") < source.index("agentFollowups.add(task.task_id)")


def test_agent_manager_uses_workspace_write_for_explicit_edit_requests(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    manager = AgentManager(EventBus(), api_base="http://127.0.0.1:8420")
    assert manager._sandbox_for("帮我修改这个项目") == "workspace-write"
    assert manager._sandbox_for("帮我改一下并更新文档") == "workspace-write"
    assert manager._sandbox_for("分析这个项目") == "read-only"


@pytest.mark.asyncio
async def test_server_handoff_submits_codex_task_with_fast_path_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    server = LampgoServer(LampgoConfig(no_hw=True))
    submitted: dict[str, Any] = {}

    class FakeManager:
        async def submit_task(self, payload: dict[str, Any]) -> dict[str, Any]:
            submitted.update(payload)
            return {"task_id": "agent-test", "status": "queued"}

    server.agent = FakeManager()  # type: ignore[assignment]
    response = await server._handoff_to_agent(
        request_id="req-test",
        text="帮我重构项目",
        reason="需要修改多个文件",
        recent_tool_calls=[{"tool_name": "camera_snap", "status": "ok"}],
    )

    assert response["result"]["source"] == "codex"
    assert response["result"]["agent_task"]["task_id"] == "agent-test"
    assert submitted["context"]["recent_tool_calls"][0]["tool_name"] == "camera_snap"
    assert "joint_positions" in submitted["context"]["current_state"]
    assert submitted["context"]["current_state"]["no_hw"] is True


@pytest.mark.asyncio
async def test_explicit_codex_summon_bypasses_fast_llm(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    server = LampgoServer(LampgoConfig(no_hw=True))
    submitted: dict[str, Any] = {}

    class FakeManager:
        async def submit_task(self, payload: dict[str, Any]) -> dict[str, Any]:
            submitted.update(payload)
            return {"task_id": "agent-direct", "status": "queued"}

    class ForbiddenFastLlm:
        async def run_agent_loop(self, *args, **kwargs):
            raise AssertionError("explicit summon must bypass the fast LLM")

    server.agent = FakeManager()  # type: ignore[assignment]
    server.router.set_llm_client(ForbiddenFastLlm())
    response = await server._handle_text(
        {"input": "把你大哥叫来，帮我重构项目", "request_id": "req-direct"}
    )

    assert response["result"]["source"] == "codex"
    assert response["result"]["agent_task"]["task_id"] == "agent-direct"
    assert submitted["reason"] == "用户明确点名调用本机 Codex"
    assert submitted["user_text"] == "把你大哥叫来，帮我重构项目"
