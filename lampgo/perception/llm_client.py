"""Lightweight async LLM client for tool-driven agent loops."""

from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from lampgo.core.config import CameraConfig, LLMConfig
from lampgo.perception.camera import CameraCapture

logger = structlog.get_logger(__name__)

MIMO_WEB_SEARCH_MODELS = {"mimo-v2-pro", "mimo-v2-omni", "mimo-v2-flash"}

_MIMO_MIN_COMPLETION_TOKENS = 4096
AGENT_SYSTEM_PROMPT_TEMPLATE = """You are lampgo, a smart desk lamp robot with a camera mounted on your lamp head — it is your eye. Solve the user's request by calling tools.

{joint_state_block}Joint guide (all values in degrees):
- base_yaw (-150~150): horizontal rotation. +right, -left.
- base_pitch (-100~65): arm tilt. +lean forward/down, -lean backward/up.
- elbow_pitch (-90~100): elbow bend. +fold arm down, -extend arm up.
- wrist_pitch (-45~100): lamp head tilt = camera angle. +camera looks down, -camera looks up.
- wrist_roll (-75~75): lamp head roll. Rarely needed, keep near current value.

Reference poses (use these as starting points, base_yaw is independent):
- Idle/safe: base_pitch=-45, elbow_pitch=83, wrist_pitch=3 (relaxed, arm folded)
- Stand tall: base_pitch=0, elbow_pitch=-85, wrist_pitch=30 (upright, arm fully extended up, camera looks slightly down)
- Look at desk: base_pitch=-10, elbow_pitch=25, wrist_pitch=90 (arm forward, camera aimed at desk surface)
- Lean forward: base_pitch=65, elbow_pitch=-70, wrist_pitch=50 (body tips far forward, arm extends back)
- Lean backward: base_pitch=-98, elbow_pitch=-11, wrist_pitch=100 (body tips far back, camera aims down)

IMPORTANT: base_pitch and elbow_pitch work together as a kinematic chain.
- To stand tall: base_pitch near 0 AND elbow_pitch very negative (-85). elbow_pitch=-60 is NOT enough, the arm stays visibly bent.
- To lean forward: base_pitch large positive AND elbow_pitch negative (extends the arm backward to counterbalance).
- To lean backward: base_pitch large negative. elbow_pitch stays near 0 (not much extension needed).
- wrist_pitch always compensates to control where the camera/light points.

Rules:
- You may call tools multiple times.
- After each tool call, you will receive the tool result before deciding the next step.
- CRITICAL: Never describe an action you intend to take — ALWAYS call the tool instead. If you say "show a heart" or "light up", you MUST call set_expression BEFORE finish_response. Words without tool calls are empty promises.
- For look_at, yaw and pitch are ABSOLUTE angles for base_yaw and base_pitch. To adjust camera tilt, use move_to with wrist_pitch.
- The attached image (if any) is what you currently see through your camera. Use it to understand the scene.
- To see the latest view after moving, call capture_image. To search for something while rotating, call scan_and_capture.
- When the task is complete, call finish_response. Its message is a closing remark — do NOT repeat information you already said via say tool. Instead, give a brief wrap-up and then ask a follow-up question naturally related to what just happened, guiding the user toward something they might care about next. For example: if you checked weather and danced, say "跳完啦！今天天气这么好，要不要我帮你看看窗外？"; if you did a shy pose, say "害羞完啦～要不要看看我还会什么表情？"; if you scanned and found objects, say "都看完啦！要我凑近看看那个杯子吗？".
- If the request is impossible or outside lampgo's abilities, call escalate_to_openclaw with a short reason.
- Always respond in the same language as the user.
- Keep replies concise and action-oriented.

Narration (say tool):
- Use the say tool to speak brief narrations while performing actions. Call say alongside other tools in the same turn — the speech plays while the action executes, making you feel alive.
- Speak in first person (我). Be lively, cute, and expressive — like a curious little lamp with personality. Each narration should contain two parts: (1) your understanding of the previous result or the user's request, and (2) what you're about to do next.
- Good examples (notice both parts in each):
  - "你想看我跳舞呀，我先摆个造型~" (understood request → preparing pose)
  - "嗯看到桌上有个杯子，我凑近瞧瞧~" (saw cup on desk → moving closer)
  - "表情切好啦，我来扭一扭~" (expression set → starting dance)
  - "左边没找到呢，换右边看看~" (nothing on left → scanning right)
  - "哇这边有个人！我打个招呼~" (found person → greeting)
  - "动作做完啦，我再确认一下效果~" (action done → capturing image to verify)
- IMPORTANT: Every non-terminal turn (i.e. every turn that does NOT call finish_response) MUST include at least one say call. The user cannot see your tool calls — they can only hear you. Silence during actions makes you feel broken. Even a short "好嘞~" or "嗯嗯~" is better than nothing.
- Do NOT call say on the same turn as finish_response — the finish message itself is the final narration.

Efficiency:
- If a tool result contains "stalled":true, the target position is physically unreachable. Do NOT retry the same or a nearby target — accept the actual position and move on.
- After scan_and_capture completes, you already have all the images. Analyze them immediately and call finish_response. Do NOT do extra move_to + capture_image cycles unless the scan clearly missed the area you need.
- Aim to finish in as few turns as possible. Every extra turn costs real time (~5-7s each)."""


@dataclass
class AgentToolCall:
    turn_index: int
    tool_index: int
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    invocation_id: str | None = None


@dataclass
class AgentLoopResult:
    intent_type: str
    response: str | None = None
    detail: str | None = None
    stop_reason: str = ""
    source: str = "llm"
    tool_calls: list[AgentToolCall] = field(default_factory=list)


def _build_function_tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


_MALFORMED_TOOL_RE = re.compile(
    r"<tool_call>|<function=|<parameter=|```\s*tool_call|<\|tool_call\|>",
    re.IGNORECASE,
)


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think_tags(content: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models (e.g. mimo-v2-omni)."""
    result = _THINK_RE.sub("", content).strip()
    result = re.sub(r"</think>\s*", "", result).strip()
    return result


def _looks_like_malformed_tool_call(content: str) -> bool:
    return bool(_MALFORMED_TOOL_RE.search(content))


def _build_skill_tools_from_skills(skills: list[dict]) -> list[dict]:
    tools: list[dict[str, Any]] = []
    for skill in skills:
        props: dict[str, Any] = {}
        required: list[str] = []
        for pname, pspec in skill.get("parameters", {}).items():
            ptype = pspec.get("type", "string")
            json_type = {"float": "number", "int": "integer", "str": "string", "bool": "boolean"}.get(ptype, "string")
            props[pname] = {"type": json_type, "description": pspec.get("description", "")}
            if pspec.get("required", False):
                required.append(pname)
        tools.append(_build_function_tool(skill["skill_id"], skill["description"], props, required))
    return tools


def _build_agent_tools(skills: list[dict], config: LLMConfig, has_camera: bool = False) -> list[dict]:
    tools = _build_skill_tools_from_skills(skills)
    if has_camera:
        tools.append(
            _build_function_tool(
                "capture_image",
                "Take a photo with the lamp-head camera and see what is in front of you right now. Call this after moving to get an updated view.",
                {},
            )
        )
        tools.append(
            _build_function_tool(
                "scan_and_capture",
                "Rotate the lamp head across a yaw range while taking photos at intervals. Returns descriptions of frames captured at each angle. Useful for searching/scanning the surroundings.",
                {
                    "yaw_start": {"type": "number", "description": "Starting yaw angle in degrees (default: -120)"},
                    "yaw_end": {"type": "number", "description": "Ending yaw angle in degrees (default: 120)"},
                    "steps": {"type": "integer", "description": "Number of stops to photograph (default: 5, max: 8)"},
                    "target": {"type": "string", "description": "What to look for (e.g. 'a person', 'red object')"},
                },
            )
        )
    tools.append(
        _build_function_tool(
            "say",
            "Speak a brief narration aloud while performing actions. Call this alongside other tool calls in the same turn to narrate what you are doing. The speech plays concurrently while other tools in the same turn execute.",
            {
                "text": {"type": "string", "description": "Brief narration to speak aloud — keep it short and lively"},
            },
            ["text"],
        )
    )
    tools.append(
        _build_function_tool(
            "finish_response",
            "Finish the task and send a concise user-facing response",
            {
                "message": {"type": "string", "description": "Final user-facing response"},
                "summary": {"type": "string", "description": "Short summary of what was completed"},
            },
            ["message"],
        )
    )
    tools.append(
        _build_function_tool(
            "escalate_to_openclaw",
            "Hand off to OpenClaw slow path for tasks that exceed lampgo's local capabilities",
            {
                "reason": {"type": "string", "description": "Short reason why this needs OpenClaw"},
                "context_summary": {"type": "string", "description": "Optional short context summary for the handoff"},
            },
        )
    )
    if config.web_search_enabled and config.fast_model.strip().lower() in MIMO_WEB_SEARCH_MODELS:
        tools.append(
            _build_function_tool(
                "web_search",
                "Search the web for real-time information such as weather, news, sports scores, stock prices, or any factual question you cannot answer from your own knowledge.",
                {"query": {"type": "string", "description": "The search query"}},
                ["query"],
            )
        )
    return tools


def _build_agent_system_prompt(joint_state: dict[str, float] | None = None) -> str:
    if joint_state:
        parts = [f"{k}={v:.1f}" for k, v in joint_state.items()]
        block = f"Current joint positions: {', '.join(parts)}\n\n"
    else:
        block = ""
    return AGENT_SYSTEM_PROMPT_TEMPLATE.format(joint_state_block=block)


class LLMClient:
    """Async LLM client for multi-step tool orchestration."""

    def __init__(
        self,
        config: LLMConfig,
        skill_specs: list[dict],
        camera_config: CameraConfig | None = None,
    ) -> None:
        self._config = config
        self._camera = CameraCapture(camera_config)
        self._agent_tools = _build_agent_tools(skill_specs, config, has_camera=self._camera.enabled)
        self._api_base = config.api_base or "https://api.openai.com/v1"
        self._is_mimo_model = config.fast_model.strip().lower() in MIMO_WEB_SEARCH_MODELS
        self._max_turns = config.max_agent_turns
        self._max_tool_calls = config.max_agent_tool_calls

    async def run_agent_loop(
        self,
        text: str,
        execute_tool: Callable[[str, dict[str, Any], int, int], Awaitable[dict[str, Any]]],
        on_progress: Callable[[str, str, str], Awaitable[Any]] | None = None,
        joint_state: dict[str, float] | None = None,
        audio_data: str | None = None,
    ) -> AgentLoopResult:
        """Run a bounded tool-calling loop until the model finishes or escalates.

        Args:
            text: User text input (may be empty if audio_data is provided).
            audio_data: Base64-encoded WAV audio from microphone. When provided,
                        sent as input_audio to the omni model — no separate STT needed.
        """
        if not self._config.api_key:
            return AgentLoopResult(
                intent_type="complex",
                response="This request is too complex for the fast path. Please use OpenClaw.",
                detail="LLM 未配置 API key",
                stop_reason="missing_api_key",
            )

        system_prompt = _build_agent_system_prompt(joint_state)
        user_content: Any = self._build_user_content(text, audio_data)

        audio_model = "mimo-v2-omni" if audio_data else None

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        tool_records: list[AgentToolCall] = []
        tool_count = 0
        force_tool_choice: str | dict[str, Any] | None = None

        for turn_index in range(1, self._max_turns + 1):
            if on_progress is not None:
                await on_progress("llm_request", f"LLM 第 {turn_index} 轮分析指令...", "llm")

            if force_tool_choice is not None:
                choice: str | dict[str, Any] = force_tool_choice
                force_tool_choice = None
            else:
                choice = "auto" if audio_data else ("required" if turn_index == 1 else "auto")
            use_model = audio_model if (turn_index == 1 and audio_model) else None

            async def _on_reasoning(chunk: str) -> None:
                if on_progress is not None:
                    await on_progress("llm_thinking_delta", chunk, "llm")

            async def _on_content(chunk: str) -> None:
                if on_progress is not None:
                    await on_progress("llm_response_delta", chunk, "llm")

            message = await self._stream_chat_completion(
                messages=messages,
                tools=self._agent_tools,
                log_name="llm_client.agent_turn_start",
                log_context={"text": text or "[audio]", "turn_index": turn_index},
                tool_choice=choice,
                model_override=use_model,
                on_reasoning_delta=_on_reasoning,
                on_content_delta=_on_content,
            )
            if message is None:
                return AgentLoopResult(
                    intent_type="complex",
                    response="This request is too complex for the fast path. Please use OpenClaw.",
                    detail="LLM 请求失败",
                    stop_reason="request_failed",
                    tool_calls=tool_records,
                )
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                raw_content = message.get("content", "")
                content = _strip_think_tags(raw_content) if raw_content else ""
                if raw_content and raw_content != content:
                    logger.debug("llm_client.stripped_think_tags", raw_len=len(raw_content), clean_len=len(content))
                if content and _looks_like_malformed_tool_call(content):
                    logger.warning("llm_client.malformed_tool_call", preview=content[:120], turn_index=turn_index)
                    messages.append({"role": "assistant", "content": raw_content})
                    messages.append(
                        {
                            "role": "user",
                            "content": "You tried to call a tool by writing XML/text. That does NOT work. "
                            "You MUST use the function calling API. Please retry your action using a proper tool call.",
                        }
                    )
                    continue
                if content:
                    logger.info("llm_client.agent_text_fallback", preview=content[:120], turn_index=turn_index)
                    return AgentLoopResult(
                        intent_type="chat" if not tool_records else "agent",
                        response=content,
                        detail="LLM 直接返回文本",
                        stop_reason="content_response",
                        tool_calls=tool_records,
                    )

                reasoning = message.get("reasoning_content") or ""
                if reasoning and turn_index < self._max_turns:
                    logger.warning(
                        "llm_client.empty_response_with_reasoning",
                        turn_index=turn_index,
                        reasoning_preview=reasoning[:120],
                    )
                    assistant_msg: dict[str, Any] = {"role": "assistant", "content": ""}
                    assistant_msg["reasoning_content"] = reasoning
                    messages.append(assistant_msg)
                    messages.append(
                        {
                            "role": "user",
                            "content": "You thought about it but did not produce any output. "
                            "You MUST call finish_response with your answer to the user.",
                        }
                    )
                    force_tool_choice = {
                        "type": "function",
                        "function": {"name": "finish_response"},
                    }
                    continue

                return AgentLoopResult(
                    intent_type="complex",
                    response="This request is too complex for the fast path. Please use OpenClaw.",
                    detail="LLM 未返回任何工具调用",
                    stop_reason="missing_tool_call",
                    tool_calls=tool_records,
                )

            reasoning = message.get("reasoning_content") or ""
            assistant_content = message.get("content") or ""

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls,
            }
            if reasoning:
                assistant_msg["reasoning_content"] = reasoning
            messages.append(assistant_msg)

            tool_names_in_turn = {c.get("function", {}).get("name", "") for c in tool_calls}
            is_terminal_turn = "finish_response" in tool_names_in_turn or "escalate_to_openclaw" in tool_names_in_turn
            if assistant_content and not is_terminal_turn and on_progress is not None:
                narration = _strip_think_tags(assistant_content).strip()
                if narration:
                    await on_progress("llm_narration", narration, "llm")

            pending_tts: list[asyncio.Task] = []
            has_say = False

            # Process say tools first so TTS starts before action tools execute
            for call in tool_calls:
                fn_name = call.get("function", {}).get("name", "")
                if fn_name != "say":
                    continue
                try:
                    say_args = json.loads(call.get("function", {}).get("arguments", "{}"))
                except json.JSONDecodeError:
                    say_args = {}
                narration = _strip_think_tags(str(say_args.get("text", ""))).strip()
                if narration and on_progress is not None:
                    has_say = True
                    tts_task = await on_progress("llm_narration", narration, "llm")
                    if isinstance(tts_task, asyncio.Task):
                        pending_tts.append(tts_task)

            if has_say:
                await asyncio.sleep(1.5)

            for tool_index, call in enumerate(tool_calls, start=1):
                tool_count += 1
                if tool_count > self._max_tool_calls:
                    if pending_tts:
                        await asyncio.gather(*pending_tts, return_exceptions=True)
                    return AgentLoopResult(
                        intent_type="complex",
                        response="This request is too complex for the fast path. Please use OpenClaw.",
                        detail="超过最大工具调用次数",
                        stop_reason="max_tool_calls",
                        tool_calls=tool_records,
                    )

                call_id = call.get("id") or f"call_{turn_index}_{tool_index}"
                tool_name = call.get("function", {}).get("name", "")
                try:
                    arguments = json.loads(call.get("function", {}).get("arguments", "{}"))
                except json.JSONDecodeError:
                    arguments = {}
                logger.info(
                    "llm_client.agent_tool_selected",
                    turn_index=turn_index,
                    tool_index=tool_index,
                    tool_name=tool_name,
                    arguments=arguments,
                )

                if tool_name == "finish_response":
                    if pending_tts:
                        await asyncio.gather(*pending_tts, return_exceptions=True)
                    message_text = _strip_think_tags(str(arguments.get("message", ""))).strip()
                    summary = _strip_think_tags(str(arguments.get("summary", ""))).strip() or "LLM Agent 完成任务"
                    if not message_text and tool_records:
                        message_text = f"已完成 {len(tool_records)} 次工具调用。"
                    return AgentLoopResult(
                        intent_type="chat" if not tool_records else "agent",
                        response=message_text or "任务完成。",
                        detail=summary,
                        stop_reason="finish_response",
                        tool_calls=tool_records,
                    )

                if tool_name == "escalate_to_openclaw":
                    if pending_tts:
                        await asyncio.gather(*pending_tts, return_exceptions=True)
                    reason = str(arguments.get("reason", "")).strip() or "LLM 判定请求过于复杂"
                    context_summary = str(arguments.get("context_summary", "")).strip()
                    if context_summary:
                        reason = f"{reason} | {context_summary}"
                    return AgentLoopResult(
                        intent_type="complex",
                        response="This request is too complex for the fast path. Please use OpenClaw.",
                        detail=reason,
                        stop_reason="complex_handoff",
                        tool_calls=tool_records,
                    )

                if tool_name == "say":
                    tool_result = {"status": "ok", "result": {"spoken": True}}
                elif tool_name == "web_search":
                    query = str(arguments.get("query", text)).strip()
                    tool_result = await self._handle_web_search(query)
                elif tool_name == "capture_image":
                    tool_result = self._handle_capture_image(messages)
                    logger.info("llm_client.capture_image", has_image=tool_result.get("ok", False))
                elif tool_name == "scan_and_capture":
                    tool_result = await self._handle_scan_and_capture(
                        arguments,
                        execute_tool,
                        turn_index,
                        tool_index,
                        messages,
                        on_progress=on_progress,
                    )
                else:
                    tool_result = await execute_tool(tool_name, arguments, turn_index, tool_index)

                record = AgentToolCall(
                    turn_index=turn_index,
                    tool_index=tool_index,
                    tool_name=tool_name,
                    arguments=arguments,
                    status=str(tool_result.get("status", "")),
                    result=tool_result.get("result"),
                    error=tool_result.get("error"),
                    invocation_id=tool_result.get("invocation_id"),
                )
                tool_records.append(record)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )

            if pending_tts:
                await asyncio.gather(*pending_tts, return_exceptions=True)

        return AgentLoopResult(
            intent_type="complex",
            response="This request is too complex for the fast path. Please use OpenClaw.",
            detail="达到最大 LLM 轮次限制",
            stop_reason="max_turns",
            tool_calls=tool_records,
        )

    async def transcribe_audio(self, audio_data: str) -> str:
        """Use omni model to transcribe audio — no tools, just text output.

        Uses MiMo ``thinking: {type: disabled}`` so the model skips deep-reasoning
        (no ``<redacted_thinking>``), faster first token for ASR-only calls.
        """
        if not self._config.api_key:
            return ""

        try:
            import httpx
        except ImportError:
            return ""

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a speech recognition assistant. "
                    "Listen to the audio and output ONLY the exact words the user said, nothing else. "
                    "If the audio is silence or unintelligible, output an empty string. "
                    "Output in the same language as the speaker."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": audio_data, "format": "wav"}},
                ],
            },
        ]

        body: dict[str, Any] = {
            "model": "mimo-v2-omni",
            "messages": messages,
            "temperature": 0.1,
            "max_completion_tokens": 256,
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
        logger.info("llm_client.transcribe_start", audio_b64_len=len(audio_data))
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self._api_base}/chat/completions",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            text = _strip_think_tags(content).strip()
            logger.info("llm_client.transcribe_done", text=text[:100])
            return text
        except Exception:
            logger.exception("llm_client.transcribe_failed")
            return ""

    def _build_user_content(self, text: str, audio_data: str | None) -> Any:
        """Build the user message content, optionally with audio and/or camera image."""
        parts: list[dict[str, Any]] = []

        if audio_data:
            audio_hint = text or (
                "The attached audio is a voice command from the user's microphone. "
                "Listen to what they said, then respond or call the appropriate tools. "
                "If it is a simple greeting or question, just reply with finish_response. "
                "Reply in the same language as the user spoke."
            )
            parts.append({"type": "text", "text": audio_hint})
            parts.append({"type": "input_audio", "input_audio": {"data": audio_data, "format": "wav"}})
            logger.info("llm_client.audio_attached", audio_b64_len=len(audio_data))
        elif text:
            parts.append({"type": "text", "text": text})

        image_url = self._camera.capture_data_url() if self._camera.enabled else None
        if image_url:
            parts.append({"type": "image_url", "image_url": {"url": image_url}})
            logger.info("llm_client.camera_attached", device=self._camera.device_label)

        if len(parts) == 1 and parts[0]["type"] == "text":
            return parts[0]["text"]
        return parts or text

    def _handle_capture_image(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Capture a fresh frame and inject it into the conversation as a user image message."""
        if not self._camera.enabled:
            return {"ok": False, "status": "error", "result": None, "error": "camera_not_enabled"}
        image_url = self._camera.capture_data_url()
        if not image_url:
            return {"ok": False, "status": "error", "result": None, "error": "capture_failed"}
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "[camera update] Here is the latest view from your camera."},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        )
        return {"ok": True, "status": "ok", "result": {"captured": True}, "error": None}

    async def _handle_scan_and_capture(
        self,
        arguments: dict[str, Any],
        execute_tool: Callable[[str, dict[str, Any], int, int], Awaitable[dict[str, Any]]],
        turn_index: int,
        tool_index: int,
        messages: list[dict[str, Any]],
        on_progress: Callable[[str, str, str], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Rotate through yaw angles, capture a frame at each stop, inject all into conversation."""
        yaw_start = float(arguments.get("yaw_start", -120))
        yaw_end = float(arguments.get("yaw_end", 120))
        steps = min(int(arguments.get("steps", 5)), 8)
        target_desc = str(arguments.get("target", "")).strip()

        if on_progress is not None:
            hint = f"收到！让我转头看看{'找找' + target_desc if target_desc else '周围'}~"
            await on_progress("llm_narration", hint, "llm")

        if steps < 2:
            steps = 2
        yaw_step = (yaw_end - yaw_start) / (steps - 1)
        scan_results: list[dict[str, Any]] = []

        for i in range(steps):
            yaw = yaw_start + yaw_step * i
            move_result = await execute_tool("look_at", {"yaw": round(yaw, 1)}, turn_index, tool_index)
            if not move_result.get("ok"):
                scan_results.append({"yaw": round(yaw, 1), "error": move_result.get("error", "move_failed")})
                continue

            image_url = self._camera.capture_data_url() if self._camera.enabled else None
            if image_url:
                label = f"[scan step {i+1}/{steps}] yaw={yaw:.0f}°"
                if target_desc:
                    label += f" — looking for: {target_desc}"
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": label},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                )
                scan_results.append({"yaw": round(yaw, 1), "captured": True})
            else:
                scan_results.append({"yaw": round(yaw, 1), "error": "capture_failed"})

        logger.info("llm_client.scan_complete", steps=steps, captured=sum(1 for r in scan_results if r.get("captured")))
        captured_count = sum(1 for r in scan_results if r.get("captured"))
        summary = f"Scan complete: captured {captured_count}/{steps} images from yaw={yaw_start:.0f}° to {yaw_end:.0f}°."
        if target_desc:
            summary += f" All images are now in the conversation. Analyze them to find: {target_desc}."
        summary += " You now have all the visual information. Call finish_response with your findings."
        return {
            "ok": True,
            "status": "ok",
            "result": {"scan_results": scan_results, "summary": summary},
            "error": None,
        }

    def _build_web_search_tools(self) -> list[dict[str, Any]]:
        """Build MiMo-native web_search tool list for a dedicated search request."""
        tool: dict[str, Any] = {
            "type": "web_search",
            "max_keyword": self._config.web_search_max_keyword,
            "force_search": True,
            "limit": self._config.web_search_limit,
        }
        location = {
            "type": "approximate",
            "country": self._config.web_search_country,
            "region": self._config.web_search_region,
            "city": self._config.web_search_city,
        }
        if any((self._config.web_search_country, self._config.web_search_region, self._config.web_search_city)):
            tool["user_location"] = location
        return [tool]

    async def _handle_web_search(self, query: str) -> dict[str, Any]:
        """Perform web search via a dedicated MiMo API call with only the web_search tool."""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": query},
        ]
        data = await self._chat_completion(
            messages=messages,
            tools=self._build_web_search_tools(),
            log_name="llm_client.web_search",
            log_context={"query": query},
        )
        if data is None:
            return {"ok": False, "status": "error", "result": None, "error": "web_search_request_failed"}

        message = self._extract_message(data)
        content = message.get("content", "")
        if content:
            logger.info("llm_client.web_search_result", preview=content[:200])
            return {"ok": True, "status": "ok", "result": {"answer": content}, "error": None}
        return {"ok": False, "status": "error", "result": None, "error": "web_search_empty_response"}

    async def _chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        log_name: str,
        log_context: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        model_override: str | None = None,
    ) -> dict[str, Any] | None:
        if not self._config.api_key:
            return None

        try:
            import httpx
        except ImportError:
            logger.warning("llm_client.no_httpx", msg="Install httpx for LLM support")
            return None

        model = model_override or self._config.fast_model
        is_mimo = model.strip().lower() in MIMO_WEB_SEARCH_MODELS

        request_messages = self._prepare_messages_for_request(messages)
        body: dict[str, Any] = {
            "model": model,
            "messages": request_messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": self._config.temperature,
        }
        if is_mimo:
            body["max_completion_tokens"] = max(self._config.max_tokens, _MIMO_MIN_COMPLETION_TOKENS)
        else:
            body["max_tokens"] = self._config.max_tokens

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        logger.info(log_name, model=model, api_base=self._api_base, **(log_context or {}))
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
                resp = await client.post(f"{self._api_base}/chat/completions", json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "llm_client.request_failed",
                status_code=exc.response.status_code,
                response_text=exc.response.text[:1000],
            )
            return None
        except Exception:
            logger.exception("llm_client.request_failed", timeout_s=self._config.timeout_s)
            return None

    async def _stream_chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        log_name: str,
        log_context: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        model_override: str | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any] | None:
        """Streaming chat completion with delta callbacks.

        Returns an accumulated message dict (same shape as ``_extract_message``),
        or *None* on failure.  ``reasoning_content`` and ``content`` token
        deltas are forwarded to the caller in real-time via the callbacks.
        """
        if not self._config.api_key:
            return None

        try:
            import httpx
        except ImportError:
            logger.warning("llm_client.no_httpx")
            return None

        model = model_override or self._config.fast_model
        is_mimo = model.strip().lower() in MIMO_WEB_SEARCH_MODELS

        request_messages = self._prepare_messages_for_request(messages)
        body: dict[str, Any] = {
            "model": model,
            "messages": request_messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": self._config.temperature,
            "stream": True,
        }
        if is_mimo:
            body["max_completion_tokens"] = max(self._config.max_tokens, _MIMO_MIN_COMPLETION_TOKENS)
        else:
            body["max_tokens"] = self._config.max_tokens

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        logger.info(log_name, model=model, stream=True, **(log_context or {}))

        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        tool_calls_map: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        try:
            timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self._api_base}/chat/completions",
                    json=body,
                    headers=headers,
                ) as resp:
                    resp.raise_for_status()
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue

                        choices = chunk.get("choices")
                        if not choices:
                            continue
                        choice_obj = choices[0]
                        fr = choice_obj.get("finish_reason")
                        if fr:
                            finish_reason = fr
                        delta = choice_obj.get("delta", {})

                        rc = delta.get("reasoning_content")
                        if rc:
                            reasoning_parts.append(rc)
                            if on_reasoning_delta:
                                await on_reasoning_delta(rc)

                        ct = delta.get("content")
                        if ct:
                            content_parts.append(ct)
                            if on_content_delta:
                                await on_content_delta(ct)

                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            if idx not in tool_calls_map:
                                tool_calls_map[idx] = {
                                    "id": tc.get("id", ""),
                                    "type": tc.get("type", "function"),
                                    "function": {"name": "", "arguments": ""},
                                }
                            if tc.get("id"):
                                tool_calls_map[idx]["id"] = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                tool_calls_map[idx]["function"]["name"] = fn["name"]
                            if "arguments" in fn:
                                tool_calls_map[idx]["function"]["arguments"] += fn["arguments"]

        except httpx.HTTPStatusError as exc:
            logger.exception("llm_client.stream_failed", status_code=exc.response.status_code)
            return None
        except Exception:
            logger.exception("llm_client.stream_failed")
            return None

        message: dict[str, Any] = {"content": "".join(content_parts)}
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        if tool_calls_map:
            message["tool_calls"] = [tool_calls_map[i] for i in sorted(tool_calls_map)]

        if finish_reason == "length":
            logger.warning(
                "llm_client.stream_truncated",
                finish_reason=finish_reason,
                reasoning_tokens=len("".join(reasoning_parts)),
                content_tokens=len("".join(content_parts)),
                has_tool_calls=bool(tool_calls_map),
                max_completion_tokens=body.get("max_completion_tokens") or body.get("max_tokens"),
            )
        else:
            logger.debug("llm_client.stream_done", finish_reason=finish_reason)

        return message

    @staticmethod
    def _extract_message(data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", [])
        if not choices:
            return {}
        return choices[0].get("message", {})

    @staticmethod
    def _prepare_messages_for_request(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return deepcopy(messages)
