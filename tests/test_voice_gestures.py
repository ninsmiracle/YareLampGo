import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from lampgo.core.config import CameraConfig, DEFAULT_JOINT_LIMITS, LLMConfig
from lampgo.core.events import EventBus
from lampgo.core.types import JointState, SkillResult
from lampgo.perception import llm_client
from lampgo.persona.bundle import MemoryBundle, PersonaBundle
from lampgo.skills.base import Skill
from lampgo.skills.builtin.parametric_skills import ConversationGestureSkill
from lampgo.skills.executor import SkillExecutor
from lampgo.skills.registry import SkillRegistry


def call(name, **args):
    return {"id": name, "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.mark.parametrize("other", ["play_recording", "nod", "show_clock", "start_electronic_ocean", "capture_image"])
def test_explicit_work_takes_priority_over_default_sway(other):
    calls = [call("say", text="好的", motion="auto"), call(other)]
    assert llm_client._expand_voice_gesture_calls(calls, used=False, available=True,
        turn_index=1, default_style="idle_sway", user_text="你好") == calls


@pytest.mark.parametrize("args", [dict(used=True), dict(available=False), dict(user_text="不要动，只说话")])
def test_no_duplicate_unavailable_or_unwanted_gesture(args):
    kwargs = dict(used=False, available=True, turn_index=1, default_style="idle_sway", user_text="你好")
    kwargs.update(args)
    calls = [call("say", text="你好")]
    assert llm_client._expand_voice_gesture_calls(calls, **kwargs) == calls


def test_explicit_no_motion_keeps_speech_and_expression():
    calls = [call("say", text="我不动，你说吧", motion="none", expression_preset="p4_attentive")]
    assert llm_client._expand_voice_gesture_calls(calls, used=False, available=True,
        turn_index=1, default_style="idle_sway", user_text="继续聊") == calls


@pytest.mark.asyncio
async def test_reply_defaults_to_face_and_alternating_gesture_in_one_model_round(monkeypatch):
    from lampgo import expression_library
    monkeypatch.setattr("lampgo.persona.bundle.load_bundles", lambda *a, **kw: (PersonaBundle(), MemoryBundle()))
    monkeypatch.setattr(expression_library, "list_expression_presets", lambda: [{"preset_id": "p4_attentive"}])
    client = llm_client.LLMClient(LLMConfig(api_key="test", web_search_enabled=False),
        [{"skill_id": name, "description": name, "parameters": {}}
         for name in ["set_expression", "conversation_gesture"]], camera_config=CameraConfig(enabled=False))
    requests, events = [], []
    async def model(**kwargs):
        requests.append(kwargs)
        return {"tool_calls": [call("say", text="你好，今天过得怎么样？", response_complete=True)]}
    async def execute(name, params, *args):
        events.append((name, params))
        return {"status": "ok"}
    async def progress(stage, text, source):
        if stage == "llm_narration":
            events.append(("speech", text))
    monkeypatch.setattr(client, "_stream_chat_completion", model)
    for _ in range(2):
        result = await client.run_agent_loop("你好", execute, on_progress=progress, call_mode=True)
        assert result.stop_reason == "spoken_response"
    assert len(requests) == 2
    assert [e[0] for e in events] == ["speech", "set_expression", "conversation_gesture"] * 2
    assert [e[1]["style"] for e in events if e[0] == "conversation_gesture"] == ["idle_sway", "playful_sway"]


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["idle_sway", "playful_sway"])
@pytest.mark.parametrize("near_limit", [False, True])
async def test_frames_start_and_end_at_pose_and_remain_small_and_smooth(style, near_limit):
    frames_seen = []
    centre = {"base_yaw": 6.4, "base_pitch": 26.2, "wrist_pitch": -5.1}
    if near_limit:
        centre = {joint: DEFAULT_JOINT_LIMITS[joint].max - 0.1 for joint in centre}
    def stream(frames, fps):
        frames_seen.extend(frames)
        done = threading.Event()
        done.set()
        return done
    motion = SimpleNamespace(stream_frames=stream)
    result = await ConversationGestureSkill().execute(
        SimpleNamespace(motion=motion, state=JointState(positions=centre)), style=style, duration=100,
    )
    assert result.status == "ok"
    assert result.data["duration"] == 8
    for joint in frames_seen[0]:
        assert frames_seen[0][joint] == frames_seen[-1][joint] == centre[joint]
        assert max(abs(f[joint] - centre[joint]) for f in frames_seen) <= 3
        assert max(abs(b[joint] - a[joint]) * 50 for a, b in zip(frames_seen, frames_seen[1:])) < 12
        assert all(DEFAULT_JOINT_LIMITS[joint].min <= f[joint] <= DEFAULT_JOINT_LIMITS[joint].max for f in frames_seen)
    assert any(frame != frames_seen[0] for frame in frames_seen)


@pytest.mark.asyncio
async def test_reply_gesture_never_cancels_busy_foreground_skill():
    started, finish = asyncio.Event(), asyncio.Event()
    class Foreground(Skill):
        skill_id = "foreground"
        async def execute(self, ctx, **params):
            started.set()
            await finish.wait()
            return SkillResult(status="ok")
        async def cancel(self):
            pytest.fail("a reply gesture must never cancel foreground work")
    registry = SkillRegistry()
    registry.register(Foreground())
    registry.register(ConversationGestureSkill())
    executor = SkillExecutor(registry, EventBus())
    ctx = SimpleNamespace(motion=SimpleNamespace(is_running=True))
    task = asyncio.create_task(executor.invoke("foreground", ctx))
    await started.wait()
    result = await executor.invoke("conversation_gesture", ctx)
    assert result.status == "rejected"
    assert not task.done()
    finish.set()
    assert (await task).status == "ok"


@pytest.mark.asyncio
async def test_interruption_stops_gesture_without_restart():
    started = asyncio.Event()
    stopped = []
    class Motion:
        is_running = True
        def stream_frames(self, frames, fps):
            started.set()
            return threading.Event()
        def stop_immediate(self):
            stopped.append(True)
    registry = SkillRegistry()
    registry.register(ConversationGestureSkill())
    executor = SkillExecutor(registry, EventBus())
    task = asyncio.create_task(executor.invoke("conversation_gesture", SimpleNamespace(
        motion=Motion(), state=JointState(positions={"base_yaw": 0, "base_pitch": 26}),
    )))
    await started.wait()
    await executor.cancel_current()
    assert (await task).status == "cancelled"
    assert stopped


@pytest.mark.asyncio
@pytest.mark.parametrize("skill_id,params", [("conversation_gesture", {}),
                                            ("set_expression", {"preserve_activity": True})])
async def test_companions_preserve_active_clock(skill_id, params):
    from lampgo.skills.builtin.expression_skills import SetExpressionSkill
    registry = SkillRegistry()
    registry.register(ConversationGestureSkill())
    registry.register(SetExpressionSkill())
    executor = SkillExecutor(registry, EventBus())
    ctx = SimpleNamespace(clock=SimpleNamespace(snapshot=lambda: {"enabled": True}))
    result = await executor.invoke(skill_id, ctx, **params)
    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_hardware_recovery_rejects_companion():
    registry = SkillRegistry()
    registry.register(ConversationGestureSkill())
    executor = SkillExecutor(registry, EventBus())
    executor.set_motion_block_reason("recovery required", allow_return_safe_recovery=True)
    result = await executor.invoke("conversation_gesture", SimpleNamespace())
    assert result.status == "rejected"
    assert result.error_code == "motor_hardware_unavailable"
