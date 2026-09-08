"""Regression coverage for MiMo speech routing and payloads."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from lampgo.core.config import LLMConfig, VoiceConfig
from lampgo.voice import mimo
from lampgo.voice.agent_sdk import AgentSDKManager
from lampgo.voice.tts import synthesize_for_web


def _llm() -> LLMConfig:
    return LLMConfig(
        provider="mimo",
        api_base="https://api.xiaomimimo.com/v1",
        api_key="mimo-key",
    )


def test_voice_config_migrates_old_speech_settings_to_mimo() -> None:
    cfg = VoiceConfig(
        stt_provider="volcengine",
        stt_model="bigmodel",
        tts_provider="edge-tts",
        tts_model="seed-tts-2.0-standard",
        tts_voice="zh_female_vv_uranus_bigtts",
        livekit_tts_voice="BV700_streaming",
    )

    assert cfg.stt_provider == "mimo"
    assert cfg.stt_model == "mimo-v2.5-asr"
    assert cfg.tts_provider == "mimo"
    assert cfg.tts_model == "mimo-v2.5-tts"
    assert cfg.tts_voice == "mimo_default"
    assert cfg.livekit_tts_voice == "mimo_default"


def test_mimo_settings_reuse_llm_base_url_and_key() -> None:
    settings = mimo.build_mimo_speech_settings(_llm(), VoiceConfig())

    assert settings.api_base == "https://api.xiaomimimo.com/v1"
    assert settings.api_key == "mimo-key"
    assert settings.asr_model == "mimo-v2.5-asr"
    assert settings.tts_model == "mimo-v2.5-tts"
    assert settings.tts_voice == "mimo_default"


def test_blank_mimo_base_url_uses_the_default_endpoint() -> None:
    assert mimo.mimo_chat_completions_url(" ") == "https://api.xiaomimimo.com/v1/chat/completions"


def test_mimo_settings_reject_non_mimo_llm_key() -> None:
    llm = _llm().model_copy(update={"provider": "openai"})
    with pytest.raises(ValueError, match="llm.provider"):
        mimo.build_mimo_speech_settings(llm, VoiceConfig())


def test_mimo_asr_uses_chat_completions_and_llm_auth(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "你好"}}]})

    _use_mock_transport(monkeypatch, handler)
    settings = mimo.build_mimo_speech_settings(_llm(), VoiceConfig())

    result = asyncio.run(mimo.transcribe_mimo_wav(settings, "UklGRg=="))

    body = captured["body"]
    assert result.text == "你好"
    assert captured["url"] == "https://api.xiaomimimo.com/v1/chat/completions"
    assert captured["headers"]["api-key"] == "mimo-key"
    assert body["model"] == "mimo-v2.5-asr"
    assert body["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/wav;base64,")


def test_mimo_tts_streams_pcm_from_openai_sse(monkeypatch) -> None:
    captured: dict[str, object] = {}
    expected_pcm = b"\x01\x00\x02\x00"
    event = {
        "choices": [{"delta": {"audio": {"data": base64.b64encode(expected_pcm).decode("ascii")}}}],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode())

    _use_mock_transport(monkeypatch, handler)
    settings = mimo.build_mimo_speech_settings(_llm(), VoiceConfig())

    async def collect() -> list[bytes]:
        return [chunk async for chunk in mimo.stream_mimo_tts_pcm(settings, "测试播报")]

    assert asyncio.run(collect()) == [expected_pcm]
    body = captured["body"]
    assert body["model"] == "mimo-v2.5-tts"
    assert body["messages"][-1] == {"role": "assistant", "content": "测试播报"}
    assert body["audio"] == {"format": "pcm16", "voice": "mimo_default"}
    assert body["stream"] is True


def test_web_tts_accepts_shared_llm_and_voice_config() -> None:
    params = synthesize_for_web.__code__.co_varnames
    assert "llm" in params
    assert "voice_config" in params
    assert "app_id" not in params
    assert "access_token" not in params


def test_agent_sdk_roles_yaml_uses_mimo_for_stt_and_tts(monkeypatch) -> None:
    monkeypatch.delenv("LAMPGO_RTC_TOKEN_API_KEY", raising=False)
    monkeypatch.delenv("LAMPGO_AGENT_REGISTRATION_TOKEN", raising=False)
    manager = AgentSDKManager(VoiceConfig(livekit_url="https://rtc.yhaox.top"), _llm())
    roles_path = manager._generate_roles_yaml()
    try:
        roles_yaml = roles_path.read_text(encoding="utf-8")
    finally:
        roles_path.unlink(missing_ok=True)

    assert 'url: "wss://rtc.yhaox.top"' in roles_yaml
    assert 'rtc_token_endpoint: "https://rtc.yhaox.top/rtc/token"' in roles_yaml
    assert 'base_url: "https://api.xiaomimimo.com/v1"' in roles_yaml
    assert 'model: "mimo-v2.5-asr"' in roles_yaml
    assert 'model: "mimo-v2.5-tts"' in roles_yaml
    assert 'voice: "mimo_default"' in roles_yaml
    assert "volcengine" not in roles_yaml.lower()


def _use_mock_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    original = mimo.httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(mimo.httpx, "AsyncClient", client)
