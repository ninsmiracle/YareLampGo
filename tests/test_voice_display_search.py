import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lampgo.core.config import CameraConfig, LLMConfig
from lampgo.core.hal import MotorStartupState
from lampgo.perception import llm_client
from lampgo.persona.bundle import PersonaBundle, MemoryBundle
from lampgo.server import LampgoServer


@pytest.mark.asyncio
async def test_call_releases_old_clock_but_preserves_emergency_stop():
    class Display:
        enabled = True
        def snapshot(self):
            return {"enabled": self.enabled}
        def deactivate(self):
            self.enabled = False
    safety = SimpleNamespace(is_estopped=lambda: False)
    server = SimpleNamespace(clock=Display(), electronic_ocean=Display(),
        lighting_mode=SimpleNamespace(is_engaged=False), safety=safety,
        config=SimpleNamespace(no_hw=False), hal=SimpleNamespace(startup_state=MotorStartupState.READY),
        executor=SimpleNamespace(invoke=AsyncMock(return_value="accepted")))
    result = await LampgoServer._invoke_lampgo_skill(server, "conversation_gesture", None, reason="test")
    assert result.status == "rejected" and "clock_active" in result.error_detail
    LampgoServer.begin_voice_display(server)
    assert await LampgoServer._invoke_lampgo_skill(server, "conversation_gesture", None, reason="test") == "accepted"
    assert await LampgoServer._invoke_lampgo_skill(server, "set_expression", None, reason="test", preserve_activity=True) == "accepted"
    safety.is_estopped = lambda: True
    result = await LampgoServer._invoke_lampgo_skill(server, "conversation_gesture", None, reason="test")
    assert result.status == "rejected" and result.error_detail == "estopped"
    assert server.executor.invoke.await_count == 2


@pytest.mark.asyncio
async def test_voice_search_times_out_and_cancels_provider(monkeypatch):
    client = llm_client.LLMClient(LLMConfig(api_key="test"), [], camera_config=CameraConfig(enabled=False))
    cancelled = asyncio.Event()
    async def slow_search(query):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(client, "_handle_web_search", slow_search)
    monkeypatch.setattr(llm_client, "_VOICE_SEARCH_TIMEOUT_S", 0.01)
    result = await client._handle_voice_web_search("北京天气")
    assert result["status"] == "error" and "timeout" in result["error"]
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_voice_turn_cannot_repeat_search_with_reworded_query(monkeypatch):
    monkeypatch.setattr("lampgo.persona.bundle.load_bundles", lambda *a, **kw: (PersonaBundle(), MemoryBundle()))
    client = llm_client.LLMClient(LLMConfig(api_key="test", web_search_enabled=True), [],
                                 camera_config=CameraConfig(enabled=False))
    searches, requests = [], []
    async def search(query):
        searches.append(query)
        return {"ok": True, "status": "ok", "result": {"answer": "天气未知，网页Loading"}}
    async def model(**kwargs):
        requests.append(kwargs)
        index = len(requests)
        if index <= 2:
            name, arguments = "web_search", {"query": "北京天气" if index == 1 else "北京今日天气温度"}
        else:
            name, arguments = "say", {"text": "暂时没查到可靠天气数据", "response_complete": True, "motion": "none"}
        return {"tool_calls": [{"id": str(index), "function": {"name": name, "arguments": json.dumps(arguments)}}]}
    monkeypatch.setattr(client, "_handle_web_search", search)
    monkeypatch.setattr(client, "_stream_chat_completion", model)
    await client.run_agent_loop("北京天气怎么样", AsyncMock(), on_progress=AsyncMock(), call_mode=True)
    assert searches == ["北京天气"]
    assert all(t["function"]["name"] != "web_search" for t in requests[1]["tools"])
