from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lampgo import expression_library
from lampgo.core.config import CameraConfig, LampgoConfig, LEDConfig, LLMConfig
from lampgo.perception import llm_client
from lampgo.persona.bundle import MemoryBundle, PersonaBundle
from lampgo.server import LampgoServer
from lampgo.skills.builtin.expression_skills import SetExpressionSkill


def _call(name, args, call_id="speech"):
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}


def test_voice_speech_requires_explicit_existing_face_or_none():
    tools = llm_client._build_agent_tools(
        [], LLMConfig(), call_mode=True, expression_preset_ids=["p4_greet", "usr_custom"],
    )
    say = next(t["function"] for t in tools if t["function"]["name"] == "say")
    assert "expression_preset" in say["parameters"]["required"]
    assert "response_complete" in say["parameters"]["required"]
    assert say["parameters"]["properties"]["expression_preset"]["enum"] == ["none", "p4_greet", "usr_custom"]
    ordinary = llm_client._build_agent_tools([], LLMConfig())
    say = next(t["function"] for t in ordinary if t["function"]["name"] == "say")
    assert "expression_preset" not in say["parameters"]["properties"]


@pytest.mark.parametrize("preset,used,other", [
    ("none", False, None), ("invented_id", False, None), ("p4_greet", True, None),
    ("p4_greet", False, "play_recording"), ("p4_greet", False, "set_expression"),
    ("p4_greet", False, "end_conversation"), ("p4_greet", False, "escalate_to_agent"),
    ("p4_greet", False, "show_clock"), ("p4_greet", False, "start_electronic_ocean"),
    ("p4_greet", False, "enter_lighting_mode"),
])
def test_speech_face_never_invents_duplicates_or_overrides_action(preset, used, other):
    calls = [_call("say", {"text": "你好", "expression_preset": preset})]
    if other:
        calls.append(_call(other, {}, "other"))
    assert llm_client._expand_voice_expression_calls(
        calls, {"p4_greet"}, expression_used=used, turn_index=1,
    ) == calls


@pytest.mark.asyncio
async def test_speech_face_runs_through_audited_skill_and_reports_failure(monkeypatch):
    monkeypatch.setattr("lampgo.persona.bundle.load_bundles", lambda *a, **kw: (PersonaBundle(), MemoryBundle()))
    monkeypatch.setattr(expression_library, "list_expression_presets", lambda: [{"preset_id": "usr_custom"}])
    client = llm_client.LLMClient(
        LLMConfig(api_key="test"), [{"skill_id": "set_expression", "description": "face", "parameters": {}}],
        camera_config=CameraConfig(enabled=False),
    )
    requests, executed, spoken = [], [], []

    async def model(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return {"tool_calls": [_call("say", {"text": "你好呀", "expression_preset": "usr_custom"})]}
        return {"tool_calls": [_call("finish_response", {"message": ""}, "done")]}

    async def execute(name, args, turn_index, tool_index):
        executed.append((name, args))
        return {"status": "error", "error": "device unavailable"}

    async def progress(stage, message, source):
        if stage == "llm_narration":
            spoken.append(message)

    monkeypatch.setattr(client, "_stream_chat_completion", model)
    result = await client.run_agent_loop("你好", execute, on_progress=progress, call_mode=True)
    assert executed == [("set_expression", {"expression": "usr_custom", "playback": "loop", "preserve_activity": True})]
    assert spoken == ["你好呀"]
    assert result.tool_calls[0].tool_name == "set_expression"
    assert result.tool_calls[0].status == "error"
    assert any(m.get("role") == "tool" and "device unavailable" in m.get("content", "")
               for m in requests[1]["messages"])


def test_expression_prompt_includes_user_preset_meaning(monkeypatch):
    monkeypatch.setattr(expression_library, "list_eyes", lambda: [])
    monkeypatch.setattr(expression_library, "list_led_effects", lambda: [])
    presets = [{"preset_id": "usr_new", "label": "赞同", "description": "微笑与点头眼神",
                "eye_clip_id": "eyes1", "led_effect_id": "mouth1"}]
    monkeypatch.setattr(expression_library, "list_expression_presets", lambda: presets)
    prompt = expression_library.build_expression_prompt()
    assert all(value in prompt for value in ["usr_new", "赞同", "微笑与点头眼神", "eyes1", "mouth1"])
    assert "set_expression(expression=<exact preset_id>" in prompt
    presets[0]["label"] = "新名称"
    assert "新名称" in expression_library.build_expression_prompt()


def test_live_activity_is_visible_to_conversation_planner(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    server = SimpleNamespace(
        config=LampgoConfig(),
        lighting_mode=SimpleNamespace(is_engaged=True),
        clock=SimpleNamespace(snapshot=lambda: {"enabled": True}),
        electronic_ocean=SimpleNamespace(snapshot=lambda: {"enabled": False}),
        executor=SimpleNamespace(current_skill_id="return_safe", is_busy=True),
        hal=SimpleNamespace(startup_state=SimpleNamespace(value="recovering")),
        safety=SimpleNamespace(is_estopped=lambda: False),
    )
    # No device I/O is needed to include the already-known activity snapshot.
    prompt = LampgoServer._recording_actions_prompt(server)
    activity = json.loads(prompt.rsplit("\n", 1)[1])
    assert activity["lighting_engaged"] and activity["clock_enabled"]
    assert activity["running_skill"] == "return_safe"
    assert activity["motor_startup_state"] == "recovering"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["set_expression", "play_recording"])
async def test_voice_reply_executes_expression_or_bound_action_in_same_round(monkeypatch, action):
    monkeypatch.setattr("lampgo.persona.bundle.load_bundles", lambda *a, **kw: (PersonaBundle(), MemoryBundle()))
    client = llm_client.LLMClient(
        LLMConfig(api_key="test", web_search_enabled=False), [], camera_config=CameraConfig(enabled=False),
        recording_actions_prompt_provider=lambda: "Saved combined presets: p4_greet\nRecorded action: 点头",
    )
    events = []
    requests = []
    params = ({"expression": "p4_greet", "playback": "once"} if action == "set_expression"
              else {"name": "点头", "expression_preset": "p4_agree"})

    async def model(**kwargs):
        requests.append(kwargs)
        return {"tool_calls": [
            {"id": "face", "function": {"name": action, "arguments": json.dumps(params)}},
            {"id": "speech", "function": {"name": "say", "arguments": '{"text":"你好呀"}'}},
            {"id": "done", "function": {"name": "finish_response", "arguments": '{"message":""}'}},
        ]}

    async def execute(name, args, turn_index, tool_index):
        events.append((name, args))
        return {"status": "ok", "result": {"accepted": True}}

    async def progress(stage, message, source):
        if stage == "llm_narration":
            events.append(("spoken", message))

    async def unexpected_sleep(delay):
        pytest.fail(f"Voice tools must not be delayed by a fixed TTS sleep: {delay}")

    monkeypatch.setattr(client, "_stream_chat_completion", model)
    monkeypatch.setattr(llm_client.asyncio, "sleep", unexpected_sleep)
    result = await client.run_agent_loop("你好", execute, on_progress=progress, call_mode=True)
    assert len(requests) == 1
    assert (action, params) in events
    assert ("spoken", "你好呀") in events
    assert result.stop_reason == "finish_response"
    assert sum(record.tool_name == action for record in result.tool_calls) == 1
    prompt = requests[0]["messages"][0]["content"]
    assert "SAME tool-call batch" in prompt
    assert "Pair ordinary spoken replies with BOTH" in prompt
    assert "Saved combined presets: p4_greet" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides,expected", [({}, "loop"), ({"playback": "once"}, "once")])
async def test_p4_preset_skill_sends_both_eyes_and_mouth(monkeypatch, tmp_path, overrides, expected):
    from lampgo.core.led import LEDController

    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    led = LEDController(LEDConfig())
    sent = []
    monkeypatch.setattr(led, "_active_device_is_p4", lambda: True)
    monkeypatch.setattr(led, "_send_remote_path", lambda path, payload, **kw: sent.append((path, payload)) or True)
    result = await SetExpressionSkill().execute(SimpleNamespace(led=led), expression="p4_greet", **overrides)
    assert result.status == "ok"
    assert sent[0][0] == "/device/expressions/play"
    assert sent[0][1]["eye_clip_id"] == "p4_greet"
    assert sent[0][1]["led_effect_id"] == "p4_greet"
    assert sent[0][1]["playback"] == expected


@pytest.mark.asyncio
async def test_voice_reply_finishes_without_extra_model_round(monkeypatch):
    monkeypatch.setattr("lampgo.persona.bundle.load_bundles", lambda *a, **kw: (PersonaBundle(), MemoryBundle()))
    monkeypatch.setattr(expression_library, "list_expression_presets", lambda: [{"preset_id": "p4_greet"}])
    client = llm_client.LLMClient(
        LLMConfig(api_key="test"), [{"skill_id": "set_expression", "description": "face", "parameters": {}}],
        camera_config=CameraConfig(enabled=False),
    )
    requests, executed, spoken = [], [], []

    async def model(**kwargs):
        requests.append(kwargs)
        assert len(requests) == 1, "Do not ask the model for an inaudible closer"
        return {"tool_calls": [_call("say", {
            "text": "你好呀", "expression_preset": "p4_greet", "response_complete": True,
        })]}

    async def execute(name, args, *indexes):
        executed.append((name, args))
        return {"status": "ok"}

    async def progress(stage, message, source):
        if stage == "llm_narration":
            spoken.append(message)

    monkeypatch.setattr(client, "_stream_chat_completion", model)
    result = await client.run_agent_loop("你好", execute, on_progress=progress, call_mode=True)
    assert executed == [("set_expression", {"expression": "p4_greet", "playback": "loop", "preserve_activity": True})]
    assert spoken == ["你好呀"]
    assert result.stop_reason == "spoken_response"
    assert result.suppress_final_tts


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["show_clock", "play_recording"])
async def test_actions_still_get_followup_without_competing_speech_face(monkeypatch, action):
    monkeypatch.setattr("lampgo.persona.bundle.load_bundles", lambda *a, **kw: (PersonaBundle(), MemoryBundle()))
    monkeypatch.setattr(expression_library, "list_expression_presets", lambda: [{"preset_id": "p4_greet"}])
    client = llm_client.LLMClient(
        LLMConfig(api_key="test"), [{"skill_id": "set_expression", "description": "face", "parameters": {}}],
        camera_config=CameraConfig(enabled=False),
    )
    requests, executed = [], []

    async def model(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return {"tool_calls": [_call(action, {}, "action")]}
        assert len(requests) == 2
        return {"tool_calls": [_call("say", {
            "text": "好了", "expression_preset": "p4_greet", "response_complete": True,
        })]}

    async def execute(name, args, *indexes):
        executed.append(name)
        return {"status": "ok"}

    async def progress(*args):
        pass

    monkeypatch.setattr(client, "_stream_chat_completion", model)
    result = await client.run_agent_loop("请执行", execute, on_progress=progress, call_mode=True)
    assert len(requests) == 2
    assert executed == [action]
    assert result.stop_reason == "spoken_response"


@pytest.mark.asyncio
async def test_interim_speech_does_not_skip_requested_action(monkeypatch):
    monkeypatch.setattr("lampgo.persona.bundle.load_bundles", lambda *a, **kw: (PersonaBundle(), MemoryBundle()))
    client = llm_client.LLMClient(
        LLMConfig(api_key="test"), [], camera_config=CameraConfig(enabled=False),
    )
    requests, executed = [], []

    async def model(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return {"tool_calls": [_call("say", {"text": "我来查一下", "response_complete": False})]}
        if len(requests) == 2:
            return {"tool_calls": [_call("show_clock", {}, "clock")]}
        assert len(requests) == 3
        return {"tool_calls": [_call("say", {"text": "显示好了", "response_complete": True})]}

    async def execute(name, args, *indexes):
        executed.append(name)
        return {"status": "ok"}

    async def progress(*args):
        pass

    monkeypatch.setattr(client, "_stream_chat_completion", model)
    result = await client.run_agent_loop("显示时钟", execute, on_progress=progress, call_mode=True)
    assert executed == ["show_clock"]
    assert result.stop_reason == "spoken_response"
