"""Tests for the upgraded IntentRouter (keyword + async aroute)."""

from __future__ import annotations

import pytest

from lampgo.core.config import CameraConfig, LLMConfig
from lampgo.perception.llm_client import LLMClient
from lampgo.perception.router import IntentRouter, IntentType, RoutedIntent


@pytest.fixture
def router():
    return IntentRouter()


def test_keyword_greeting(router):
    result = router.route("你好")
    assert result.intent_type == IntentType.CHAT
    assert result.chat_response is not None


def test_keyword_skill(router):
    result = router.route("跳舞")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "dance"


def test_keyword_expression(router):
    result = router.route("害羞")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "set_expression"
    assert result.params == {"expression": "blush"}


def test_keyword_complex_fallback(router):
    result = router.route("帮我把今天的PPT发给老板")
    assert result.intent_type == IntentType.COMPLEX


@pytest.mark.asyncio
async def test_aroute_keyword_hit(router):
    result = await router.aroute("点头")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "nod"
    assert result.source == "keyword"


@pytest.mark.asyncio
async def test_aroute_no_llm_fallback(router):
    """Without LLM client, complex intents stay complex."""
    result = await router.aroute("帮我把灯抬高一点")
    assert result.intent_type == IntentType.COMPLEX


def test_morning_greeting(router):
    result = router.route("早上好")
    assert result.intent_type == IntentType.CHAT
    assert "早" in result.chat_response


def test_estop_keyword(router):
    result = router.route("停")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "estop"


def test_return_safe_keyword(router):
    result = router.route("回家")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "return_safe"


def test_strict_keyword_does_not_match_variants(router):
    result = router.route("点个头")
    assert result.intent_type == IntentType.COMPLEX


def test_composite_request_does_not_match_keyword(router):
    result = router.route("跳个舞，唱个歌")
    assert result.intent_type == IntentType.COMPLEX
    assert result.detail == "包含复合结构，跳过关键词快路径"


@pytest.mark.asyncio
async def test_agent_loop_can_call_multiple_tools(monkeypatch):
    client = LLMClient(
        LLMConfig(api_key="test-key", web_search_enabled=False),
        skill_specs=[
            {"skill_id": "play_recording", "description": "Play recording", "parameters": {}},
            {"skill_id": "set_expression", "description": "Set expression", "parameters": {}},
        ],
    )
    scripted = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "play_recording",
                                        "arguments": "{\"name\": \"dance\"}",
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_2",
                                    "function": {
                                        "name": "set_expression",
                                        "arguments": "{\"expression\": \"music\"}",
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_3",
                                    "function": {
                                        "name": "finish_response",
                                        "arguments": (
                                            "{\"message\": \"好的，先跳舞再切到音乐表情\", "
                                            "\"summary\": \"完成两次工具调用\"}"
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        ]
    )
    progress: list[tuple[str, str, str]] = []
    executed: list[tuple[str, dict[str, object], int, int]] = []

    async def fake_chat_completion(messages, tools, log_name, log_context=None, tool_choice="auto"):
        return next(scripted)

    async def execute_tool(tool_name: str, params: dict[str, object], turn_index: int, tool_index: int):
        executed.append((tool_name, params, turn_index, tool_index))
        return {
            "ok": True,
            "status": "ok",
            "result": {"tool_name": tool_name, "params": params},
            "error": None,
            "invocation_id": f"inv_{turn_index}_{tool_index}",
        }

    async def on_progress(stage: str, message: str, source: str) -> None:
        progress.append((stage, message, source))

    monkeypatch.setattr(client, "_chat_completion", fake_chat_completion)
    result = await client.run_agent_loop("跳个舞，唱个歌", execute_tool=execute_tool, on_progress=on_progress)

    assert result.intent_type == "agent"
    assert result.response == "好的，先跳舞再切到音乐表情"
    assert result.stop_reason == "finish_response"
    assert len(result.tool_calls) == 2
    assert executed == [
        ("play_recording", {"name": "dance"}, 1, 1),
        ("set_expression", {"expression": "music"}, 2, 1),
    ]
    assert progress == [
        ("llm_request", "LLM 第 1 轮分析指令...", "llm"),
        ("llm_request", "LLM 第 2 轮分析指令...", "llm"),
        ("llm_request", "LLM 第 3 轮分析指令...", "llm"),
    ]


@pytest.mark.asyncio
async def test_agent_loop_attaches_camera_image_once(monkeypatch):
    """Camera image should be captured once and attached only to the initial user message."""
    client = LLMClient(
        LLMConfig(api_key="test-key", web_search_enabled=False),
        skill_specs=[],
        camera_config=CameraConfig(port="0"),
    )
    monkeypatch.setattr(client._camera, "capture_data_url", lambda: "data:image/jpeg;base64,abc")

    captured_bodies: list[dict] = []

    async def fake_chat_completion(self, *, messages, tools, log_name, log_context=None, tool_choice="auto"):
        from copy import deepcopy
        captured_bodies.append(deepcopy(messages))
        return {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "finish_response", "arguments": '{"message":"done"}'},
                    }],
                },
            }],
        }

    monkeypatch.setattr(LLMClient, "_chat_completion", fake_chat_completion)

    async def noop_exec(name, args, ti, toi):
        return {"ok": True, "status": "ok"}

    result = await client.run_agent_loop("看看前面", noop_exec)
    assert result.intent_type == "chat"

    assert len(captured_bodies) == 1
    user_msg = captured_bodies[0][1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][0] == {"type": "text", "text": "看看前面"}
    assert user_msg["content"][1]["type"] == "image_url"
