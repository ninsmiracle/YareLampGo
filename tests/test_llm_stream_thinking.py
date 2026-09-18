from __future__ import annotations

import json

import httpx
import pytest

from lampgo.core.config import CameraConfig, LLMConfig
from lampgo.perception.llm_client import LLMClient


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_hosted_mimo_receives_official_thinking_switch(monkeypatch, enabled):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        chunk = {"choices": [{"delta": {"content": "你好"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(handler),
    ))
    client = LLMClient(LLMConfig(api_key="test", fast_model="mimo-v2.5"), [],
                       camera_config=CameraConfig(enabled=False))
    message = await client._stream_chat_completion(
        [{"role": "user", "content": "你好"}], [], "test", enable_thinking=enabled,
    )
    assert message == {"content": "你好"}
    assert requests[0]["thinking"] == {"type": "enabled" if enabled else "disabled"}


@pytest.mark.asyncio
async def test_other_provider_does_not_receive_mimo_thinking_switch(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: [DONE]\n\n')

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(handler),
    ))
    client = LLMClient(LLMConfig(api_key="test", provider="openai", fast_model="gpt-test"), [],
                       camera_config=CameraConfig(enabled=False))
    await client._stream_chat_completion([{"role": "user", "content": "Hi"}], [], "test")
    assert "thinking" not in requests[0]


@pytest.mark.asyncio
async def test_deepseek_primary_request_uses_flash_and_its_thinking_switch(monkeypatch):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        chunk = {"choices": [{"delta": {"content": "好的"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(handler),
    ))
    client = LLMClient(
        LLMConfig(
            provider="deepseek",
            api_base="https://api.deepseek.com",
            api_key="deepseek-key",
            fast_model="deepseek-flash",
            fallback_enabled=True,
            fallback_api_key="mimo-key",
        ),
        [],
        camera_config=CameraConfig(enabled=False),
    )

    message = await client._stream_chat_completion([{"role": "user", "content": "你好"}], [], "test")

    assert message == {"content": "好的"}
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.deepseek.com/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer deepseek-key"
    body = json.loads(requests[0].content)
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_non_stream_deepseek_request_uses_the_same_safe_wire_format(monkeypatch):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "好的"}}]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(handler),
    ))
    client = LLMClient(
        LLMConfig(
            provider="deepseek",
            api_base="https://api.deepseek.com",
            api_key="deepseek-key",
            fast_model="deepseek-flash",
        ),
        [],
        camera_config=CameraConfig(enabled=False),
    )

    response = await client._chat_completion([{"role": "user", "content": "你好"}], [], "test")

    assert response == {"choices": [{"message": {"content": "好的"}}]}
    assert str(requests[0].url) == "https://api.deepseek.com/chat/completions"
    body = json.loads(requests[0].content)
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_deepseek_request_error_retries_once_on_mimo(monkeypatch):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.deepseek.com":
            raise httpx.ReadTimeout("primary unavailable", request=request)
        chunk = {"choices": [{"delta": {"content": "MiMo 接管"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(handler),
    ))
    client = LLMClient(
        LLMConfig(
            provider="deepseek",
            api_base="https://api.deepseek.com",
            api_key="deepseek-key",
            fast_model="deepseek-flash",
            fallback_enabled=True,
            fallback_after_s=6,
            fallback_provider="mimo",
            fallback_api_base="https://api.xiaomimimo.com/v1",
            fallback_api_key="mimo-key",
            fallback_model="mimo-v2.5",
        ),
        [],
        camera_config=CameraConfig(enabled=False),
    )

    message = await client._stream_chat_completion([{"role": "user", "content": "你好"}], [], "test")

    assert message == {"content": "MiMo 接管"}
    assert [request.url.host for request in requests] == ["api.deepseek.com", "api.xiaomimimo.com"]
    deepseek_body, mimo_body = (json.loads(request.content) for request in requests)
    assert deepseek_body["model"] == "deepseek-flash"
    assert deepseek_body["thinking"] == {"type": "disabled"}
    assert mimo_body["model"] == "mimo-v2.5"
    assert mimo_body["thinking"] == {"type": "disabled"}
    assert requests[1].headers["api-key"] == "mimo-key"
