from types import SimpleNamespace

import pytest

from lampgo.voice import echo_filter as echo

SPOKEN = "嘿！我是小灯，不是小度啦～不过我确实看见你了！戴眼镜、穿黑色T恤，手上还有个白色表带的手表，挺帅的嘛！"


@pytest.fixture
def context(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(echo.time, "monotonic", lambda: clock[0])
    server = SimpleNamespace(config=SimpleNamespace(voice=SimpleNamespace(
        call_mode="interruptible", echo_text_filter_enabled=True,
    )))
    return server, clock


def test_actual_log_echo_survives_tts_and_asr_delay(context):
    server, clock = context
    echo.remember_tts_text(server, SPOKEN)
    clock[0] += 18
    assert echo.filter_recent_tts_echo(server, "我有个白色表带的手表，挺帅。")[0] == ""
    assert echo.filter_recent_tts_echo(server, "挺帅的嘛，你给我打个招呼吧。")[0] == "你给我打个招呼吧。"


@pytest.mark.parametrize("text", ["你好", "停", "不要动，只说话", "白色表带的手表多少钱", "不对，我没有戴手表",
                                  "戴眼镜、穿黑色T恤，不对！", "白色表带的手表吗？", "我没有白色表带的手表"])
def test_preserve_real_questions_corrections_and_stop(context, text):
    server, _ = context
    echo.remember_tts_text(server, SPOKEN)
    kept, _ = echo.filter_recent_tts_echo(server, text)
    assert kept
    if "不对" in text:
        assert "不对" in kept


def test_matching_older_reference_does_not_erase_newer_reference(context):
    server, clock = context
    echo.remember_tts_text(server, "今天北京晴天，适合出去散步")
    clock[0] += 3
    echo.remember_tts_text(server, "明天可能下雨，记得带把雨伞")
    assert echo.likely_recent_tts_echo(server, "今天北京晴天")[0]
    assert echo.likely_recent_tts_echo(server, "记得带把雨伞")[0]


def test_disabled_expired_and_new_call_allow_repetition(context):
    server, clock = context
    echo.remember_tts_text(server, SPOKEN)
    server.config.voice.echo_text_filter_enabled = False
    assert not echo.likely_recent_tts_echo(server, "白色表带的手表")[0]
    server.config.voice.echo_text_filter_enabled = True
    clock[0] += 91
    assert not echo.likely_recent_tts_echo(server, "白色表带的手表")[0]
    echo.remember_tts_text(server, SPOKEN)
    echo.clear_recent_tts(server)
    assert not echo.likely_recent_tts_echo(server, "白色表带的手表")[0]


def test_duplicate_narration_does_not_double_estimated_playback(context):
    server, _ = context
    echo.remember_tts_text(server, SPOKEN)
    end = server._livekit_tts_expected_until
    echo.remember_tts_text(server, SPOKEN + "。")
    assert len(server._livekit_recent_tts_texts) == 1
    assert server._livekit_tts_expected_until == end


@pytest.mark.asyncio
async def test_echo_does_not_preempt_running_backend_reply(context):
    import asyncio
    import json
    from starlette.requests import Request
    from lampgo.web.llm_compat import handle_chat_completions

    server, clock = context
    echo.remember_tts_text(server, SPOKEN)
    clock[0] += 18
    server._llm_active_task = asyncio.create_task(asyncio.Event().wait())
    body = json.dumps({"messages": [{"role": "user", "content": "我有个白色表带的手表，挺帅。"}]}).encode()
    async def receive():
        return {"type": "http.request", "body": body}
    req = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(lampgo_server=server))}, receive)
    try:
        response = await handle_chat_completions(req)
        chunks = [part async for part in response.body_iterator]
        assert "[DONE]" in "".join(chunks)
        assert not server._llm_active_task.done()
    finally:
        server._llm_active_task.cancel()
        await asyncio.gather(server._llm_active_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_sdk_drops_echo_before_final_transcript(monkeypatch, context):
    from livekit import rtc
    from lampgo.voice import mimo_livekit
    from lampgo.voice.mimo import MiMoSpeechSettings

    server, _ = context
    echo.remember_tts_text(server, SPOKEN)
    monkeypatch.setattr(mimo_livekit, "_echo_state", server)
    async def transcribe(*args, **kwargs):
        return SimpleNamespace(text="我有个白色表带的手表，挺帅。", request_id="test")
    monkeypatch.setattr(mimo_livekit, "transcribe_mimo_wav", transcribe)
    stt = mimo_livekit.MiMoLiveKitSTT(settings=MiMoSpeechSettings(api_key="test", api_base="https://example.test"), language="auto")
    try:
        result = await stt.recognize(buffer=rtc.AudioFrame.create(16000, 1, 480))
        assert result.alternatives[0].text == ""
    finally:
        await stt.aclose()
