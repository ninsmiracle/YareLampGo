"""Server text-routing tests for strict router and LLM agent loop."""

from __future__ import annotations

import asyncio

import pytest

from lampgo.core.config import DeviceConfig, LampgoConfig
from lampgo.core.events import (
    AgentFinished,
    IntentProgress,
    OpenClawPromotionDecision,
    OpenClawPromotionRequested,
    OpenClawTaskUpdated,
    ToolCallFinished,
    ToolCallPlanned,
)
from lampgo.core.types import InvokeResult
from lampgo.perception.llm_client import AgentLoopResult, AgentToolCall
from lampgo.perception.router import IntentType, RoutedIntent
from lampgo.server import LampgoServer


@pytest.mark.asyncio
async def test_text_request_runs_multiple_agent_tools(monkeypatch):
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    calls: list[tuple[str, dict[str, object]]] = []
    published: list[object] = []

    async def capture(event) -> None:
        published.append(event)

    for event_type in (IntentProgress, ToolCallPlanned, ToolCallFinished, AgentFinished):
        server.events.subscribe(event_type, capture)

    class FakeRouter:
        has_llm_client = True

        def route(self, text: str) -> RoutedIntent:
            return RoutedIntent(
                intent_type=IntentType.COMPLEX,
                source="keyword",
                detail="包含复合结构，跳过关键词快路径",
            )

        async def run_agent_loop(self, text, execute_tool, on_progress=None, joint_state=None):
            if on_progress is not None:
                await on_progress("llm_fallback", "关键词未命中，转交 LLM Agent...", "llm")
            first = await execute_tool("play_recording", {"name": "dance"}, 1, 1)
            second = await execute_tool("set_expression", {"expression": "music"}, 2, 1)
            return AgentLoopResult(
                intent_type="agent",
                response="好的，先跳舞再切到音乐表情",
                detail="完成两次工具调用",
                stop_reason="finish_response",
                tool_calls=[
                    AgentToolCall(
                        turn_index=1,
                        tool_index=1,
                        tool_name="play_recording",
                        arguments={"name": "dance"},
                        status=first["status"],
                        result=first["result"],
                        invocation_id=first["invocation_id"],
                    ),
                    AgentToolCall(
                        turn_index=2,
                        tool_index=1,
                        tool_name="set_expression",
                        arguments={"expression": "music"},
                        status=second["status"],
                        result=second["result"],
                        invocation_id=second["invocation_id"],
                    ),
                ],
            )

    async def fake_invoke(skill_id, ctx, **params):
        calls.append((skill_id, params))
        return InvokeResult(
            invocation_id=f"inv_{len(calls)}",
            status="ok",
            result={"executed": skill_id, "params": params},
        )

    server.router = FakeRouter()
    monkeypatch.setattr(server.executor, "invoke", fake_invoke)

    response = await server.handle_request(
        {"cmd": "text", "input": "跳个舞，唱个歌", "request_id": "req_123"}
    )

    assert response["ok"] is True
    assert response["result"]["type"] == "agent"
    assert response["result"]["response"] == "好的，先跳舞再切到音乐表情"
    assert len(response["result"]["tool_calls"]) == 2
    assert calls == [
        ("play_recording", {"name": "dance"}),
        ("set_expression", {"expression": "music"}),
    ]
    assert [type(event).__name__ for event in published] == [
        "IntentProgress",
        "IntentProgress",
        "ToolCallPlanned",
        "ToolCallFinished",
        "ToolCallPlanned",
        "ToolCallFinished",
        "AgentFinished",
    ]


@pytest.mark.asyncio
async def test_text_request_handoff_to_openclaw_task():
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))

    response = await server.handle_request(
        {"cmd": "text", "input": "设计一个创新动作", "request_id": "req_openclaw"}
    )

    assert response["ok"] is True
    assert response["result"]["type"] == "openclaw"
    task_id = response["result"]["openclaw_task"]["task_id"]

    await asyncio.sleep(0.01)
    task = server.openclaw.get_task(task_id)
    assert task is not None
    assert task["status"] == "awaiting_promotion_confirmation"
    assert len(task["proposals"]) == 1
    assert task["proposals"][0]["proposal_type"] == "recording_proposal"


@pytest.mark.asyncio
async def test_openclaw_promotion_confirmation_updates_task():
    server = LampgoServer(LampgoConfig(device=DeviceConfig(motor_port="/dev/null")))
    published: list[object] = []

    async def capture(event) -> None:
        published.append(event)

    for event_type in (OpenClawTaskUpdated, OpenClawPromotionRequested, OpenClawPromotionDecision):
        server.events.subscribe(event_type, capture)

    task = await server.openclaw.submit_complex_task(
        {
            "request_id": "req_confirm",
            "user_text": "设计一个创新动作",
            "reason": "需要新的舞台动作",
            "available_capabilities": [],
            "recent_tool_calls": [],
        }
    )
    await asyncio.sleep(0.01)
    updated = server.openclaw.get_task(task["task_id"])
    assert updated is not None
    proposal_id = updated["proposals"][0]["proposal_id"]

    confirmed = await server.openclaw.confirm_promotion(task["task_id"], proposal_id, "approve")

    assert confirmed["status"] == "promoted"
    assert confirmed["proposals"][0]["status"] == "approved"
    assert [type(event).__name__ for event in published][-2:] == [
        "OpenClawPromotionDecision",
        "OpenClawTaskUpdated",
    ]
