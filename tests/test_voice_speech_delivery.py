import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from lampgo.core.config import CameraConfig, LLMConfig
from lampgo.perception.llm_client import LLMClient
from lampgo.persona.bundle import MemoryBundle, PersonaBundle
from lampgo.skills.builtin.expression_skills import SetExpressionSkill


def say(text, index=0):
    return {"id": f"say-{index}", "function": {"name": "say", "arguments": json.dumps({
        "text": text, "response_complete": True, "expression_preset": "none", "motion": "none",
    })}}


@pytest.mark.asyncio
@pytest.mark.parametrize("content,speeches,expected", [
    ("找到你了！黑T恤、眼镜。", ["找到你了！黑T恤、眼镜。"], ["找到你了！黑T恤、眼镜。"]),
    ("模型额外的普通正文", ["正式回复。"], ["正式回复。"]),
    ("", ["第一句。", "第二句。"], ["第一句。", "第二句。"]),
    ("", ["你好！", "你好。"], ["你好！"]),
    ("保留无 say 的正常回复", [], ["保留无 say 的正常回复"]),
    ("保留空 say 的正文", [""], ["保留空 say 的正文"]),
])
async def test_one_model_response_has_one_authoritative_speech_channel(monkeypatch, content, speeches, expected):
    monkeypatch.setattr("lampgo.persona.bundle.load_bundles", lambda *a, **kw: (PersonaBundle(), MemoryBundle()))
    monkeypatch.setattr("lampgo.expression_library.list_expression_presets", lambda: [])
    client = LLMClient(LLMConfig(api_key="test", web_search_enabled=False), [],
                       camera_config=CameraConfig(enabled=False))
    requests, spoken = [], []
    async def model(**kwargs):
        requests.append(kwargs)
        if len(requests) > 1:
            return {"tool_calls": [{"id": "finish", "function": {"name": "finish_response",
                                     "arguments": '{"message":""}'}}]}
        return {"content": content, "tool_calls": [say(t, i) for i, t in enumerate(speeches)]}
    async def execute(*a):
        return {"status": "ok"}
    async def progress(stage, text, source):
        if stage == "llm_narration":
            spoken.append(text)
    monkeypatch.setattr(client, "_stream_chat_completion", model)
    result = await client.run_agent_loop("找到我", execute, call_mode=True, on_progress=progress)
    assert spoken == expected
    assert result.spoken_texts == expected
    if speeches and any(speeches):
        assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("expression", ["p4_joy", "happy"])
async def test_slow_expression_io_does_not_stop_audio_event_loop(expression):
    entered, release = threading.Event(), threading.Event()
    def wait_io(*args, **kwargs):
        entered.set()
        if not release.wait(1):
            raise TimeoutError("event loop could not run while LED HTTP was pending")
        return True
    def play(*args, **kwargs):
        return wait_io(), {"preset_id": expression}
    led = SimpleNamespace(play_expression=play, set_mode=wait_io, set_brightness=wait_io)
    task = asyncio.create_task(SetExpressionSkill().execute(SimpleNamespace(led=led), expression=expression))
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        # This task stands in for the mic-forward/ack coroutine on the same loop.
        await asyncio.sleep(0)
        assert not task.done(), "LED I/O blocked the audio relay event loop"
        release.set()
        assert (await task).status == "ok"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
