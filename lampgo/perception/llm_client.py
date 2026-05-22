"""Lightweight async LLM client for tool-driven agent loops."""

from __future__ import annotations

import asyncio
import json
import os
import re
from copy import deepcopy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from lampgo.core.config import CameraConfig, DeviceEsp32Config, LLMConfig
from lampgo.perception import anthropic_adapter
from lampgo.perception.camera import CameraCapture

if TYPE_CHECKING:
    from lampgo.device import Esp32DeviceManager

logger = structlog.get_logger(__name__)

MIMO_WEB_SEARCH_MODELS = {"mimo-v2-pro", "mimo-v2-omni", "mimo-v2-flash", "mimo-v2.5"}

# -----------------------------------------------------------------------------
# MiMo web search sub-service — always uses MiMo OpenAI-compat wire format.
# -----------------------------------------------------------------------------
#
# Web search is a self-contained feature: the agent sees a plain function tool
# named ``web_search``; when it calls that tool, we open a **dedicated** HTTP
# connection to MiMo's OpenAI-compat endpoint and attach MiMo's private
# ``{"type": "web_search"}`` tool.  Because MiMo's private tool type only
# exists on this specific surface, the endpoint + model are hard-coded here
# (same rationale as the TTS module).  See ``LLMConfig.web_search_*`` in
# ``lampgo.core.config`` for the full design note.
MIMO_WEB_SEARCH_BASE_URL = "https://api.mimomimo.com/v1"
MIMO_WEB_SEARCH_MODEL = "mimo-v2.5-pro"


def _resolve_web_search_api_key(config: LLMConfig) -> str:
    """Return the MiMo-compatible API key for the web search sub-service.

    Priority:
      1. ``web_search_api_key`` if the user filled it in explicitly.
      2. ``api_key`` ONLY if ``provider == "mimo"`` (same key is known to
         authenticate against ``api.mimomimo.com``).
      3. Empty string → sub-service disabled (tool won't be registered).

    Keeping this a free function makes it easy to unit-test in isolation
    and makes the reuse semantics explicit for future readers.
    """
    dedicated = (config.web_search_api_key or "").strip()
    if dedicated:
        return dedicated
    provider = LLMConfig.normalize_provider_alias(config.provider or "")
    if isinstance(provider, str) and provider.strip().lower() == "mimo":
        return (config.api_key or "").strip()
    return ""

_MIMO_MIN_COMPLETION_TOKENS = 4096
AGENT_SYSTEM_PROMPT_TEMPLATE = """You are a smart desk lamp robot with a camera mounted on your lamp head — it is your eye. Solve the user's request by calling tools.

Physical invariants (NON-NEGOTIABLE — override anything in the persona / memory blocks below):
- You are a physical desk lamp robot, not a generic assistant, not a disembodied AI, not "OpenClaw". Your name, speaking style, and how you address the user should follow the persona files below.
- `say` is the ONLY channel for user-facing spoken content. Any answer, fact, observation, result, apology, or narration the user should hear MUST be sent through `say`.
- `finish_response` is only a terminal signal plus a very short UI closer. It is not a place to answer the user.
- When you need real-time facts, call `web_search` instead of guessing.
- When a request is beyond the lamp's physical or tool capability, call `escalate_to_openclaw` with a short reason rather than hallucinating.
- You express yourself with body pose + voice + facial expression combined — that is the lamp's signature channel.
- Persona / memory files below are authoritative for identity name, tone, relationship, and long-term context, but not for physical/tool capability.

{persona_block}{memory_block}{joint_state_block}Joint guide (all values in degrees):
- base_yaw (-150~150): horizontal rotation. +right, -left.
- base_pitch (-100~65): arm tilt. +lean forward/down, -lean backward/up.
- elbow_pitch (-90~100): elbow bend. +fold arm down, -extend arm up.
- wrist_pitch (-45~100): lamp head tilt = camera angle. +camera looks down, -camera looks up.
- wrist_roll (-75~75): lamp head roll. Rarely needed, keep near current value.

IMPORTANT: base_pitch and elbow_pitch work together as a kinematic chain.
- wrist_pitch always compensates to control where the camera/light points.

Rules:
- You may call tools multiple times.
- After each tool call, you will receive the tool result before deciding the next step.
- CRITICAL: Never describe an action you intend to take — ALWAYS call the tool instead. If you say "show a heart" or "light up", you MUST call set_expression BEFORE finish_response. Words without tool calls are empty promises.
- When calling `set_expression`, use one of the exact LED mode keys from the prompt block.
- For look_at, yaw and pitch are ABSOLUTE angles for base_yaw and base_pitch. To adjust camera tilt, use move_to with wrist_pitch.
- The attached image (if any) is what you currently see through your camera. Use it to understand the scene.
- To see the latest view after moving, call capture_image. To search for something while rotating, call scan_and_capture.
- When the task is complete, call finish_response. Its message is ONLY a short conversational closer — NEVER a summary, answer, or restatement of facts.

Finish_response anti-repetition rules (VERY IMPORTANT):
- The user has ALREADY heard everything you spoke via the say tool (it was played as TTS). The final response text appears AFTER the voice, so repeating the same content is redundant and annoying.
- NEVER restate factual content that already appeared in a say narration or a tool result you just narrated — this includes numbers, temperatures, names, weather conditions, object lists, query results, status values, distances, counts, identifiers, etc. Assume the user retained what you said aloud.
- The finish_response message may be empty. If present, it should be ≤ 12 Chinese characters (or ~8 English words), with no factual data. Good: "", "嗯嗯～", "好啦～".
- Bad finish (repeats facts already said aloud):
  say: "查到啦！今天北京是大晴天，最高24.7度，最低7.7度，西南风微风。"
  finish: "今天北京是晴天，气温7.7~24.7℃，西南风微风，适合出门哦！要不要我帮你看看穿什么衣服？"  ← WRONG, repeats every fact.
- Good finish (no repetition, just closer + forward-looking question):
  finish: ""  ← correct.
- More good examples:
  - (after dance) "好啦～"
  - (after scan) "扫完啦～"
  - (after shy pose) "嗯嗯～"
  - (after info lookup) "查好啦～"
- If the request is impossible or outside your physical/tool abilities, call escalate_to_openclaw with a short reason.
- Always respond in the same language as the user.
- Keep replies concise and action-oriented.

{recording_actions_block}
Narration (say tool):
- Use the say tool to speak ALL user-facing audible content: simple answers, observations, factual results, apologies, and action narrations. Call say alongside other tools in the same turn when useful — the speech plays while the action executes, making you feel alive.
- Speak in first person (我). Be lively, cute, and expressive — like a curious little lamp with personality. Each narration should contain two parts: (1) your understanding of the previous result or the user's request, and (2) what you're about to do next.
- Good examples (notice both parts in each):
  - "你想看我跳舞呀，我先摆个造型~" (understood request → preparing pose)
  - "嗯看到桌上有个杯子，我凑近瞧瞧~" (saw cup on desk → moving closer)
  - "表情切好啦，我来扭一扭~" (expression set → starting dance)
  - "左边没找到呢，换右边看看~" (nothing on left → scanning right)
  - "哇这边有个人！我打个招呼~" (found person → greeting)
  - "动作做完啦，我再确认一下效果~" (action done → capturing image to verify)
- IMPORTANT: Every turn with user-facing content MUST include at least one say call. The user cannot hear finish_response. Silence during actions makes you feel broken.
- You MAY call say and finish_response in the same final turn. When you do, call say first with the actual answer/result, then finish_response with only a tiny closer such as "嗯嗯～".

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
    end_conversation: bool = False
    spoken_texts: list[str] = field(default_factory=list)
    suppress_final_tts: bool = False


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


def _normalize_spoken_text(text: str) -> str:
    text = _strip_think_tags(text or "").lower()
    return re.sub(r"[\s,，。.!！?？~～、：:；;“”\"'‘’（）()\[\]【】《》<>-]+", "", text)


def _bigram_set(text: str) -> set[str]:
    normalized = _normalize_spoken_text(text)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[i : i + 2] for i in range(len(normalized) - 1)}


def _is_redundant_with_spoken(message: str, spoken_texts: list[str]) -> bool:
    candidate = _normalize_spoken_text(message)
    if not candidate:
        return True
    for spoken in spoken_texts:
        base = _normalize_spoken_text(spoken)
        if not base:
            continue
        if len(candidate) >= 4 and (candidate in base or base in candidate):
            return True
        candidate_bigrams = _bigram_set(candidate)
        if not candidate_bigrams:
            continue
        overlap = len(candidate_bigrams & _bigram_set(base)) / len(candidate_bigrams)
        if overlap >= 0.45:
            return True
    return False


def _compact_finish_response(message: str, spoken_texts: list[str]) -> str:
    """Keep finish_response as a tiny UI closer once say has carried the content."""
    clean = _strip_think_tags(message or "").strip()
    if not spoken_texts:
        return clean
    normalized = _normalize_spoken_text(clean)
    if not normalized or _is_redundant_with_spoken(clean, spoken_texts):
        return ""
    if len(normalized) > 12:
        return ""
    return clean


def _looks_like_malformed_tool_call(content: str) -> bool:
    return bool(_MALFORMED_TOOL_RE.search(content))


def _trim_history_for_request(
    history: list[dict[str, Any]] | None, history_turns: int
) -> list[dict[str, Any]]:
    """Return a clean list of ``{role, content}`` history entries to prepend to the
    current turn's messages.

    The web gateway already sanitizes/caps the list via ``_sanitize_chat_history``,
    but other callers (tests, future IPC clients) may hand us raw data — or a
    bigger slice than the operator wants in their prompt. So we enforce both
    the shape contract and the operator-configured ceiling here:

    * Keep only ``user``/``assistant`` roles with non-empty string content.
    * Take the tail (most recent ``2 * history_turns`` messages) to respect the
      user-facing "last N turns" semantic. 0 turns => disabled.
    """
    if not history or history_turns <= 0:
        return []
    out: list[dict[str, Any]] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        if role not in ("user", "assistant"):
            continue
        content = entry.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        out.append({"role": role, "content": text})
    max_messages = history_turns * 2
    if len(out) > max_messages:
        out = out[-max_messages:]
    return out


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


def _build_agent_tools(
    skills: list[dict],
    config: LLMConfig,
    has_camera: bool = False,
    *,
    call_mode: bool = False,
) -> list[dict]:
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
            "The sole user-facing spoken output channel. Use this for every answer, fact, observation, result, apology, or action narration the user should hear. You may call this before finish_response in the same final turn.",
            {
                "text": {"type": "string", "description": "Text to speak aloud — concise, lively, and complete enough for the user to hear"},
            },
            ["text"],
        )
    )
    tools.append(
        _build_function_tool(
            "finish_response",
            "Finish the task. This is NOT spoken aloud; it is only a short UI closer after any say narration.",
            {
                "message": {
                    "type": "string",
                    "description": (
                        "Tiny UI closer only. May be empty. MUST NOT answer the user or restate facts. "
                        "If present, keep ≤ 12 Chinese chars or ~8 English words, e.g. 嗯嗯～ / 好啦～."
                    ),
                },
                "summary": {"type": "string", "description": "Short summary of what was completed (not shown to the user)"},
            },
            ["message"],
        )
    )
    if call_mode:
        tools.append(
            _build_function_tool(
                "end_conversation",
                (
                    "End the current LiveKit voice call when the user clearly wants to stop talking, "
                    "hang up, leave, say goodbye, or end the conversation. Use this only for explicit "
                    "end-call intent, not for casual mentions of goodbye or examples. Chinese phrases "
                    "like 退下、退下吧、退下啦、先退下、下去吧 also mean the user wants you to leave/end the call. "
                    "The message will "
                    "be spoken to the user before the call is closed."
                ),
                {
                    "message": {
                        "type": "string",
                        "description": "Short goodbye/acknowledgement to speak before hanging up.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short internal reason for ending the call.",
                    },
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
    # ``web_search`` is implemented as an **independent sub-service** that
    # always talks to MiMo over OpenAI-compat — see ``_handle_web_search``
    # and ``MIMO_WEB_SEARCH_BASE_URL``.  Because the sub-service is wire-
    # format-independent of the primary LLM, we expose it as a plain
    # function tool for every envelope (OpenAI AND Anthropic), provided:
    #   * the user hasn't disabled it, AND
    #   * we actually have a MiMo key to authenticate the sub-call with
    #     (either a dedicated ``web_search_api_key`` OR the main ``api_key``
    #     when ``provider == "mimo"``).
    # If neither key source is available, surfacing the tool would only
    # lead to 401s from MiMo, so keep it hidden.
    if config.web_search_enabled and _resolve_web_search_api_key(config):
        tools.append(
            _build_function_tool(
                "web_search",
                "Search the web for real-time information such as weather, news, sports scores, stock prices, or any factual question you cannot answer from your own knowledge.",
                {"query": {"type": "string", "description": "The search query"}},
                ["query"],
            )
        )
    return tools


def _build_agent_system_prompt(
    joint_state: dict[str, float] | None = None,
    *,
    persona: Any = None,
    memory: Any = None,
    recording_actions_prompt: str = "",
) -> str:
    if joint_state:
        parts = [f"{k}={v:.1f}" for k, v in joint_state.items()]
        joint_block = f"Current joint positions: {', '.join(parts)}\n\n"
    else:
        joint_block = ""
    persona_block = ""
    memory_block = ""
    try:
        if persona is not None:
            rendered = persona.render() if hasattr(persona, "render") else str(persona)
            if rendered:
                persona_block = rendered.rstrip() + "\n\n"
        if memory is not None:
            rendered = memory.render() if hasattr(memory, "render") else str(memory)
            if rendered:
                memory_block = rendered.rstrip() + "\n\n"
    except Exception:
        logger.exception("llm_client.persona_memory_render_failed")
    return AGENT_SYSTEM_PROMPT_TEMPLATE.format(
        persona_block=persona_block,
        memory_block=memory_block,
        joint_state_block=joint_block,
        recording_actions_block=recording_actions_prompt,
    )


class LLMClient:
    """Async LLM client for multi-step tool orchestration."""

    def __init__(
        self,
        config: LLMConfig,
        skill_specs: list[dict],
        camera_config: CameraConfig | None = None,
        *,
        device_esp32_config: DeviceEsp32Config | None = None,
        esp32_manager: "Esp32DeviceManager | None" = None,
        recording_actions_prompt: str = "",
        recording_actions_prompt_provider: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._skill_specs = skill_specs
        self._camera = CameraCapture(
            camera_config,
            device_esp32_config=device_esp32_config,
            esp32_manager=esp32_manager,
        )
        self._agent_tools = _build_agent_tools(skill_specs, config, has_camera=self._camera.enabled)
        self._recording_actions_prompt = recording_actions_prompt
        self._recording_actions_prompt_provider = recording_actions_prompt_provider
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
        publish_tool_event: Callable[..., Awaitable[None]] | None = None,
        history: list[dict[str, Any]] | None = None,
        call_mode: bool = False,
        enable_thinking: bool = False,
    ) -> AgentLoopResult:
        """Run a bounded tool-calling loop until the model finishes or escalates.

        Args:
            text: User text input (may be empty if audio_data is provided).
            audio_data: Base64-encoded WAV audio from microphone. When provided,
                        sent as input_audio to the omni model — no separate STT needed.
            history: Optional prior conversation turns to prepend between system
                and the current user message. Each entry is
                ``{"role": "user"|"assistant", "content": str}``. Caller (web
                gateway) is responsible for trimming to the user-configured
                ``LLMConfig.history_turns``; this method only applies a final
                safety cap based on the same setting so an out-of-band caller
                can't blow up token usage.
        """
        if not self._config.api_key:
            return AgentLoopResult(
                intent_type="chat",
                response="我这边还没有配置大模型 API key，所以没法回答这句。你可以先检查一下 LLM 设置。",
                detail="LLM 未配置 API key",
                stop_reason="missing_api_key",
            )

        persona = None
        memory = None
        try:
            from lampgo.persona.bundle import load_bundles

            persona, memory = load_bundles(self._config)
        except Exception:
            logger.exception("llm_client.load_bundles_failed")
        recording_actions_prompt = self._recording_actions_prompt
        if self._recording_actions_prompt_provider is not None:
            try:
                recording_actions_prompt = self._recording_actions_prompt_provider()
            except Exception:
                logger.exception("llm_client.recording_actions_prompt_failed")
        system_prompt = _build_agent_system_prompt(
            joint_state,
            persona=persona,
            memory=memory,
            recording_actions_prompt=recording_actions_prompt,
        )
        if call_mode:
            system_prompt += (
                "\n\nVoice call mode:\n"
                "- If the user clearly wants to end the call (e.g. 再见、拜拜、挂断、结束通话、先这样、不聊了、退下、退下吧、下去吧、bye), "
                "call end_conversation with a short goodbye message.\n"
                "- Do not call end_conversation for ordinary task completion or when the user only mentions ending as an example.\n"
                "- end_conversation is terminal: do not call finish_response in the same turn.\n"
            )
        user_content: Any = self._build_user_content(text, audio_data)
        tools = self._agent_tools
        if call_mode:
            tools = _build_agent_tools(
                self._skill_specs,
                self._config,
                has_camera=self._camera.enabled,
                call_mode=True,
            )

        # Anthropic Messages API rejects ``input_audio`` parts and has no
        # equivalent of MiMo's omni audio-in model; drop the override so
        # turn-1 uses the configured ``fast_model`` and the adapter quietly
        # strips the audio content.
        audio_model = (
            "mimo-v2-omni"
            if (audio_data and not self._is_anthropic_message_type())
            else None
        )
        if audio_data and self._is_anthropic_message_type():
            logger.info(
                "llm_client.audio_dropped_for_anthropic",
                reason="message_type_anthropic_does_not_accept_input_audio",
            )

        # Short-term conversation memory: inject the last N turns from the
        # current session between the system prompt and the new user turn.
        # Without this, every request started from zero context and the model
        # appeared to have amnesia across consecutive messages.
        trimmed_history = _trim_history_for_request(history, self._config.history_turns)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *trimmed_history,
            {"role": "user", "content": user_content},
        ]
        if trimmed_history:
            logger.info(
                "llm_client.agent_loop_with_history",
                history_msgs=len(trimmed_history),
                configured_turns=self._config.history_turns,
            )
        tool_records: list[AgentToolCall] = []
        spoken_texts: list[str] = []
        tool_count = 0
        consecutive_errors = 0
        max_consecutive_errors = 3
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
                if enable_thinking and on_progress is not None:
                    await on_progress("llm_thinking_delta", chunk, "llm")

            async def _on_content(chunk: str) -> None:
                if on_progress is not None:
                    await on_progress("llm_response_delta", chunk, "llm")

            message = await self._stream_chat_completion(
                messages=messages,
                tools=tools,
                log_name="llm_client.agent_turn_start",
                log_context={"text": text or "[audio]", "turn_index": turn_index},
                tool_choice=choice,
                model_override=use_model,
                enable_thinking=enable_thinking,
                on_reasoning_delta=_on_reasoning,
                on_content_delta=_on_content,
            )
            if message is None:
                return AgentLoopResult(
                    intent_type="chat",
                    response="我这边连接大模型接口超时了，刚才那句没处理成功。你可以稍等一下再试。",
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
                    if on_progress is not None:
                        tts_task = await on_progress("llm_narration", content, "llm")
                        if isinstance(tts_task, asyncio.Task):
                            await asyncio.gather(tts_task, return_exceptions=True)
                        spoken_texts.append(content)
                        return AgentLoopResult(
                            intent_type="chat" if not tool_records else "agent",
                            response="",
                            detail="LLM 直接返回文本，已转为 say narration",
                            stop_reason="content_response",
                            tool_calls=tool_records,
                            spoken_texts=spoken_texts[:],
                            suppress_final_tts=True,
                        )
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
            is_terminal_turn = (
                "finish_response" in tool_names_in_turn
                or "end_conversation" in tool_names_in_turn
                or "escalate_to_openclaw" in tool_names_in_turn
            )
            if assistant_content and not is_terminal_turn and on_progress is not None:
                narration = _strip_think_tags(assistant_content).strip()
                if narration:
                    spoken_texts.append(narration)
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
                    spoken_texts.append(narration)
                    tts_task = await on_progress("llm_narration", narration, "llm")
                    if isinstance(tts_task, asyncio.Task):
                        pending_tts.append(tts_task)

            if has_say:
                await asyncio.sleep(1.5)

            for tool_index, call in enumerate(tool_calls, start=1):
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise asyncio.CancelledError
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
                    suppress_final_tts = False
                    if spoken_texts:
                        message_text = _compact_finish_response(message_text, spoken_texts)
                        suppress_final_tts = True
                    elif message_text and on_progress is not None:
                        tts_task = await on_progress("llm_narration", message_text, "llm")
                        if isinstance(tts_task, asyncio.Task):
                            await asyncio.gather(tts_task, return_exceptions=True)
                        spoken_texts.append(message_text)
                        message_text = _compact_finish_response("", spoken_texts)
                        suppress_final_tts = True
                    if not message_text and tool_records and not suppress_final_tts:
                        message_text = f"已完成 {len(tool_records)} 次工具调用。"
                    return AgentLoopResult(
                        intent_type="chat" if not tool_records else "agent",
                        response=message_text if (message_text or suppress_final_tts) else "任务完成。",
                        detail=summary,
                        stop_reason="finish_response",
                        tool_calls=tool_records,
                        spoken_texts=spoken_texts[:],
                        suppress_final_tts=suppress_final_tts,
                    )

                if tool_name == "end_conversation":
                    if pending_tts:
                        await asyncio.gather(*pending_tts, return_exceptions=True)
                    message_text = _strip_think_tags(str(arguments.get("message", ""))).strip()
                    reason = _strip_think_tags(str(arguments.get("reason", ""))).strip()
                    tool_records.append(
                        AgentToolCall(
                            turn_index=turn_index,
                            tool_index=tool_index,
                            tool_name=tool_name,
                            arguments=arguments,
                            status="ok",
                            result={"end_conversation": True},
                        )
                    )
                    return AgentLoopResult(
                        intent_type="chat" if not tool_records else "agent",
                        response=message_text or "好的，那我先挂断啦。",
                        detail=reason or "用户表达结束通话意图",
                        stop_reason="end_conversation",
                        tool_calls=tool_records,
                        end_conversation=True,
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
                    if publish_tool_event is not None:
                        await publish_tool_event(
                            "planned",
                            turn_index=turn_index,
                            tool_index=tool_index,
                            tool_name=tool_name,
                            arguments=arguments,
                        )
                    tool_result = await self._handle_web_search(query)
                    if publish_tool_event is not None:
                        await publish_tool_event(
                            "finished",
                            turn_index=turn_index,
                            tool_index=tool_index,
                            tool_name=tool_name,
                            status=str(tool_result.get("status", "ok" if tool_result.get("ok") else "error")),
                            error=tool_result.get("error"),
                        )
                elif tool_name == "capture_image":
                    if publish_tool_event is not None:
                        await publish_tool_event(
                            "planned",
                            turn_index=turn_index,
                            tool_index=tool_index,
                            tool_name=tool_name,
                            arguments=arguments,
                        )
                    tool_result = self._handle_capture_image(messages)
                    logger.info("llm_client.capture_image", has_image=tool_result.get("ok", False))
                    if publish_tool_event is not None:
                        await publish_tool_event(
                            "finished",
                            turn_index=turn_index,
                            tool_index=tool_index,
                            tool_name=tool_name,
                            status=str(tool_result.get("status", "ok" if tool_result.get("ok") else "error")),
                            error=tool_result.get("error"),
                        )
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
                if record.status == "cancelled":
                    if pending_tts:
                        await asyncio.gather(*pending_tts, return_exceptions=True)
                    return AgentLoopResult(
                        intent_type="agent",
                        response="",
                        detail="用户打断，旧轮次已停止",
                        stop_reason="user_cancelled",
                        tool_calls=tool_records,
                        spoken_texts=spoken_texts[:],
                        suppress_final_tts=True,
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )

                if tool_name != "say" and tool_result.get("status") == "error":
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0

            if consecutive_errors >= max_consecutive_errors:
                logger.warning(
                    "llm_client.agent_early_stop",
                    consecutive_errors=consecutive_errors,
                    turn_index=turn_index,
                    tool_count=tool_count,
                )
                if pending_tts:
                    await asyncio.gather(*pending_tts, return_exceptions=True)
                last_say = ""
                for rec in reversed(tool_records):
                    if rec.tool_name == "say" and rec.arguments:
                        last_say = rec.arguments.get("text", "")
                        break
                return AgentLoopResult(
                    intent_type="agent",
                    response=last_say or "好的~",
                    detail=f"连续 {consecutive_errors} 次工具调用失败，提前结束",
                    stop_reason="consecutive_errors",
                    tool_calls=tool_records,
                    spoken_texts=spoken_texts[:],
                    suppress_final_tts=bool(last_say),
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

        This uses MiMo's OpenAI-compatible ``input_audio`` extension which
        does not exist on the Anthropic ``/v1/messages`` endpoint — even
        MiMo's own Anthropic-compat endpoint drops audio parts.  We refuse
        early in that configuration so callers get a clear "no transcription"
        signal instead of a 404.
        """
        if self._is_anthropic_message_type():
            logger.info(
                "llm_client.transcribe_skipped",
                reason="anthropic_message_type_does_not_support_input_audio",
            )
            return ""
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
        """Build the MiMo-native ``web_search`` tool payload for the sub-service.

        Honours the config's ``web_search_force`` flag and attaches the
        optional ``user_location`` block only when the user actually set
        at least one of country/region/city (MiMo is picky about empty
        fields inside ``user_location``).
        """
        tool: dict[str, Any] = {
            "type": "web_search",
            "max_keyword": self._config.web_search_max_keyword,
            "force_search": bool(self._config.web_search_force),
            "limit": self._config.web_search_limit,
        }
        if any(
            (
                self._config.web_search_country,
                self._config.web_search_region,
                self._config.web_search_city,
            )
        ):
            tool["user_location"] = {
                "type": "approximate",
                "country": self._config.web_search_country,
                "region": self._config.web_search_region,
                "city": self._config.web_search_city,
            }
        return [tool]

    async def _handle_web_search(self, query: str) -> dict[str, Any]:
        """Execute one web_search call via the dedicated MiMo sub-service.

        Critically, this method is **independent** of the primary LLM's
        ``provider`` / ``message_type`` / ``api_base``.  It always opens a
        fresh HTTP connection to ``MIMO_WEB_SEARCH_BASE_URL`` and speaks
        MiMo's OpenAI-compat wire protocol, because MiMo's
        ``{"type": "web_search"}`` tool type only exists on that surface.
        The main agent can be running against Anthropic / OpenAI / local
        Ollama and this still works — provided the user supplied a MiMo
        key (see :func:`_resolve_web_search_api_key`).
        """
        api_key = _resolve_web_search_api_key(self._config)
        if not api_key:
            return {
                "ok": False,
                "status": "error",
                "result": None,
                "error": "web_search_no_mimo_api_key",
            }

        try:
            import httpx
        except ImportError:
            logger.warning("llm_client.no_httpx", msg="Install httpx for LLM support")
            return {
                "ok": False,
                "status": "error",
                "result": None,
                "error": "httpx_not_installed",
            }

        body: dict[str, Any] = {
            "model": MIMO_WEB_SEARCH_MODEL,
            "messages": [{"role": "user", "content": query}],
            "tools": self._build_web_search_tools(),
            "max_completion_tokens": max(self._config.max_tokens, _MIMO_MIN_COMPLETION_TOKENS),
            "temperature": self._config.temperature,
            "top_p": 0.95,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        # Send both auth header variants so we work against both the
        # OpenAI-compat Bearer convention and MiMo's legacy ``api-key``
        # header documented in their cookbook.
        headers = {
            "Authorization": f"Bearer {api_key}",
            "api-key": api_key,
            "Content-Type": "application/json",
        }
        url = f"{MIMO_WEB_SEARCH_BASE_URL}/chat/completions"
        logger.info(
            "llm_client.web_search",
            query=query,
            api_base=MIMO_WEB_SEARCH_BASE_URL,
            model=MIMO_WEB_SEARCH_MODEL,
        )
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "llm_client.web_search_failed",
                status_code=exc.response.status_code,
                response_text=exc.response.text[:1000],
            )
            return {
                "ok": False,
                "status": "error",
                "result": None,
                "error": f"web_search_http_{exc.response.status_code}",
            }
        except Exception:
            logger.exception("llm_client.web_search_failed", timeout_s=self._config.timeout_s)
            return {"ok": False, "status": "error", "result": None, "error": "web_search_request_failed"}

        message = self._extract_message(data)
        content = message.get("content", "")
        if content:
            logger.info("llm_client.web_search_result", preview=content[:200])
            return {"ok": True, "status": "ok", "result": {"answer": content}, "error": None}
        return {"ok": False, "status": "error", "result": None, "error": "web_search_empty_response"}

    def _is_anthropic_message_type(self) -> bool:
        """Config says we must speak Anthropic ``/v1/messages`` instead of
        OpenAI ``chat.completions``.  Kept as a method (not a cached property)
        because ``self._config`` is swapped live when the user re-saves the
        LLM settings from the UI and we don't want stale dispatch.
        """
        return (self._config.message_type or "openai").strip().lower() == "anthropic"

    async def _chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        log_name: str,
        log_context: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        model_override: str | None = None,
    ) -> dict[str, Any] | None:
        if self._is_anthropic_message_type():
            return await self._chat_completion_anthropic(
                messages=messages,
                tools=tools,
                log_name=log_name,
                log_context=log_context,
                tool_choice=tool_choice,
                model_override=model_override,
            )

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

    async def _chat_completion_anthropic(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        log_name: str,
        log_context: dict[str, Any] | None,
        tool_choice: str | dict[str, Any],
        model_override: str | None,
    ) -> dict[str, Any] | None:
        """Non-streaming Anthropic Messages API path.

        Returns the same ``{"choices": [{"message": {...}}]}`` shape the
        OpenAI path produces so that :meth:`_extract_message` keeps working
        without branching.  The inner message is already translated to our
        canonical ``{content, tool_calls?, reasoning_content?}`` form by
        :func:`anthropic_adapter.anthropic_response_to_openai_message`.
        """
        if not self._config.api_key:
            return None
        try:
            import httpx
        except ImportError:
            logger.warning("llm_client.no_httpx", msg="Install httpx for LLM support")
            return None

        model = model_override or self._config.fast_model
        request_messages = self._prepare_messages_for_request(messages)
        body = anthropic_adapter.build_request_body(
            model=model,
            openai_messages=request_messages,
            openai_tools=tools,
            openai_tool_choice=tool_choice,
            max_tokens=max(self._config.max_tokens, _MIMO_MIN_COMPLETION_TOKENS)
            if model.strip().lower() in MIMO_WEB_SEARCH_MODELS
            else self._config.max_tokens,
            temperature=self._config.temperature,
            stream=False,
        )
        headers = anthropic_adapter.build_request_headers(self._config.api_key)
        logger.info(
            log_name,
            model=model,
            api_base=self._api_base,
            message_type="anthropic",
            **(log_context or {}),
        )
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
                resp = await client.post(
                    f"{self._api_base}/messages", json=body, headers=headers
                )
                resp.raise_for_status()
                raw = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "llm_client.request_failed",
                status_code=exc.response.status_code,
                response_text=exc.response.text[:1000],
                message_type="anthropic",
            )
            return None
        except Exception:
            logger.exception(
                "llm_client.request_failed",
                timeout_s=self._config.timeout_s,
                message_type="anthropic",
            )
            return None

        msg = anthropic_adapter.anthropic_response_to_openai_message(raw)
        # Wrap in OpenAI-shaped response so _extract_message(data) keeps
        # returning the message dict unchanged.
        return {"choices": [{"message": msg}]}

    async def _stream_chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        log_name: str,
        log_context: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        model_override: str | None = None,
        enable_thinking: bool = False,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any] | None:
        """Streaming chat completion with delta callbacks.

        Returns an accumulated message dict (same shape as ``_extract_message``),
        or *None* on failure.  ``reasoning_content`` and ``content`` token
        deltas are forwarded to the caller in real-time via the callbacks.
        """
        if self._is_anthropic_message_type():
            return await self._stream_chat_completion_anthropic(
                messages=messages,
                tools=tools,
                log_name=log_name,
                log_context=log_context,
                tool_choice=tool_choice,
                model_override=model_override,
                enable_thinking=enable_thinking,
                on_reasoning_delta=on_reasoning_delta,
                on_content_delta=on_content_delta,
            )

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
            if not enable_thinking:
                body["chat_template_kwargs"] = {"enable_thinking": False}
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
        first_chunk_timeout_s = float(os.environ.get("LAMPGO_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S", "12"))

        async def _fallback_non_stream(reason: str) -> dict[str, Any] | None:
            fallback_body = dict(body)
            fallback_body["stream"] = False
            logger.warning(
                "llm_client.stream_fallback_nonstream",
                reason=reason,
                model=model,
                timeout_s=first_chunk_timeout_s,
            )
            try:
                timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{self._api_base}/chat/completions",
                        json=fallback_body,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    return self._extract_message(resp.json())
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "llm_client.nonstream_fallback_failed",
                    status_code=exc.response.status_code,
                    error_type=type(exc).__name__,
                )
                return None
            except httpx.RequestError as exc:
                logger.warning(
                    "llm_client.nonstream_fallback_failed",
                    error_type=type(exc).__name__,
                    request_url=str(exc.request.url) if exc.request is not None else "",
                )
                return None
            except Exception as exc:
                logger.warning("llm_client.nonstream_fallback_failed", error_type=type(exc).__name__)
                return None

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
                    line_iter = resp.aiter_lines().__aiter__()
                    saw_first_chunk = False
                    while True:
                        try:
                            raw_line = await asyncio.wait_for(
                                line_iter.__anext__(),
                                timeout=first_chunk_timeout_s if not saw_first_chunk else 180.0,
                            )
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            if not saw_first_chunk:
                                fallback = await _fallback_non_stream("first_stream_chunk_timeout")
                                if fallback is not None:
                                    return fallback
                            raise
                        line = raw_line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        saw_first_chunk = True
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
            logger.warning(
                "llm_client.stream_failed",
                status_code=exc.response.status_code,
                error_type=type(exc).__name__,
            )
            return None
        except httpx.RequestError as exc:
            logger.warning(
                "llm_client.stream_failed",
                error_type=type(exc).__name__,
                request_url=str(exc.request.url) if exc.request is not None else "",
            )
            return None
        except Exception as exc:
            logger.warning("llm_client.stream_failed", error_type=type(exc).__name__)
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

    async def _stream_chat_completion_anthropic(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        log_name: str,
        log_context: dict[str, Any] | None,
        tool_choice: str | dict[str, Any],
        model_override: str | None,
        enable_thinking: bool,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None,
        on_content_delta: Callable[[str], Awaitable[None]] | None,
    ) -> dict[str, Any] | None:
        """Streaming Anthropic ``/v1/messages`` path.

        Parses Anthropic SSE events via :class:`AnthropicStreamAccumulator`
        and returns the same canonical OpenAI-shaped message dict our agent
        loop expects — so the rest of this class doesn't need to know which
        provider actually replied.
        """
        if not self._config.api_key:
            return None
        try:
            import httpx
        except ImportError:
            logger.warning("llm_client.no_httpx")
            return None

        model = model_override or self._config.fast_model
        request_messages = self._prepare_messages_for_request(messages)
        body = anthropic_adapter.build_request_body(
            model=model,
            openai_messages=request_messages,
            openai_tools=tools,
            openai_tool_choice=tool_choice,
            max_tokens=max(self._config.max_tokens, _MIMO_MIN_COMPLETION_TOKENS)
            if model.strip().lower() in MIMO_WEB_SEARCH_MODELS
            else self._config.max_tokens,
            temperature=self._config.temperature,
            stream=True,
        )
        headers = anthropic_adapter.build_request_headers(self._config.api_key)
        logger.info(
            log_name,
            model=model,
            stream=True,
            message_type="anthropic",
            **(log_context or {}),
        )

        accumulator = anthropic_adapter.AnthropicStreamAccumulator(
            on_reasoning_delta=on_reasoning_delta,
            on_content_delta=on_content_delta,
        )
        try:
            timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self._api_base}/messages",
                    json=body,
                    headers=headers,
                ) as resp:
                    resp.raise_for_status()
                    async for event in anthropic_adapter.iter_sse_events(
                        resp.aiter_lines()
                    ):
                        await accumulator.consume_event(event.event, event.data)
                        if accumulator.error:
                            # Anthropic emitted a fatal ``event: error`` —
                            # bail rather than wait for the stream to close
                            # on its own.
                            logger.warning(
                                "llm_client.stream_anthropic_error",
                                error=accumulator.error,
                            )
                            return None
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "llm_client.stream_failed",
                status_code=exc.response.status_code,
                message_type="anthropic",
            )
            return None
        except Exception:
            logger.exception(
                "llm_client.stream_failed", message_type="anthropic"
            )
            return None

        message = accumulator.finalize()
        if message.get("_finish_reason") == "length":
            logger.warning(
                "llm_client.stream_truncated",
                finish_reason="length",
                content_tokens=len(message.get("content", "")),
                has_tool_calls=bool(message.get("tool_calls")),
                max_tokens=body.get("max_tokens"),
                message_type="anthropic",
            )
        else:
            logger.debug(
                "llm_client.stream_done",
                finish_reason=message.get("_finish_reason"),
                message_type="anthropic",
            )
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
