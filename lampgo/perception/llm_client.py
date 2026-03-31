"""Lightweight async LLM client for tool-driven agent loops."""

from __future__ import annotations

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
- When the task is complete, call finish_response with a short user-facing message.
- If the request is impossible or outside lampgo's abilities, call __complex__ with a short reason.
- Always respond in the same language as the user.
- Keep replies concise and action-oriented.

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
            "__complex__",
            "Hand off to the slow path for tasks that exceed lampgo's local capabilities",
            {"reason": {"type": "string", "description": "Short reason why this needs the complex path"}},
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
        on_progress: Callable[[str, str, str], Awaitable[None]] | None = None,
        joint_state: dict[str, float] | None = None,
    ) -> AgentLoopResult:
        """Run a bounded tool-calling loop until the model finishes or escalates."""
        if not self._config.api_key:
            return AgentLoopResult(
                intent_type="complex",
                response="This request is too complex for the fast path. Please use OpenClaw.",
                detail="LLM 未配置 API key",
                stop_reason="missing_api_key",
            )

        system_prompt = _build_agent_system_prompt(joint_state)
        user_content: Any = text
        image_url = self._camera.capture_data_url() if self._camera.enabled else None
        if image_url:
            user_content = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
            logger.info("llm_client.camera_attached", device=self._camera.device_label)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        tool_records: list[AgentToolCall] = []
        tool_count = 0

        for turn_index in range(1, self._max_turns + 1):
            if on_progress is not None:
                await on_progress("llm_request", f"LLM 第 {turn_index} 轮分析指令...", "llm")

            choice = "required" if turn_index == 1 else "auto"
            data = await self._chat_completion(
                messages=messages,
                tools=self._agent_tools,
                log_name="llm_client.agent_turn_start",
                log_context={"text": text, "turn_index": turn_index},
                tool_choice=choice,
            )
            if data is None:
                return AgentLoopResult(
                    intent_type="complex",
                    response="This request is too complex for the fast path. Please use OpenClaw.",
                    detail="LLM 请求失败",
                    stop_reason="request_failed",
                    tool_calls=tool_records,
                )

            message = self._extract_message(data)
            tool_calls = message.get("tool_calls", [])
            if not tool_calls:
                content = message.get("content", "")
                if content and _looks_like_malformed_tool_call(content):
                    logger.warning("llm_client.malformed_tool_call", preview=content[:120], turn_index=turn_index)
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": "You tried to call a tool by writing XML/text. That does NOT work. "
                        "You MUST use the function calling API. Please retry your action using a proper tool call.",
                    })
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
                return AgentLoopResult(
                    intent_type="complex",
                    response="This request is too complex for the fast path. Please use OpenClaw.",
                    detail="LLM 未返回任何工具调用",
                    stop_reason="missing_tool_call",
                    tool_calls=tool_records,
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": tool_calls,
                }
            )

            for tool_index, call in enumerate(tool_calls, start=1):
                tool_count += 1
                if tool_count > self._max_tool_calls:
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
                    message_text = str(arguments.get("message", "")).strip()
                    summary = str(arguments.get("summary", "")).strip() or "LLM Agent 完成任务"
                    if not message_text and tool_records:
                        message_text = f"已完成 {len(tool_records)} 次工具调用。"
                    return AgentLoopResult(
                        intent_type="chat" if not tool_records else "agent",
                        response=message_text or "任务完成。",
                        detail=summary,
                        stop_reason="finish_response",
                        tool_calls=tool_records,
                    )

                if tool_name == "__complex__":
                    reason = str(arguments.get("reason", "")).strip() or "LLM 判定请求过于复杂"
                    return AgentLoopResult(
                        intent_type="complex",
                        response="This request is too complex for the fast path. Please use OpenClaw.",
                        detail=reason,
                        stop_reason="complex_handoff",
                        tool_calls=tool_records,
                    )

                if tool_name == "web_search":
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

        return AgentLoopResult(
            intent_type="complex",
            response="This request is too complex for the fast path. Please use OpenClaw.",
            detail="达到最大 LLM 轮次限制",
            stop_reason="max_turns",
            tool_calls=tool_records,
        )

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
    ) -> dict[str, Any]:
        """Rotate through yaw angles, capture a frame at each stop, inject all into conversation."""
        yaw_start = float(arguments.get("yaw_start", -120))
        yaw_end = float(arguments.get("yaw_end", 120))
        steps = min(int(arguments.get("steps", 5)), 8)
        target_desc = str(arguments.get("target", "")).strip()

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
        tool_choice: str = "auto",
    ) -> dict[str, Any] | None:
        if not self._config.api_key:
            return None

        try:
            import httpx
        except ImportError:
            logger.warning("llm_client.no_httpx", msg="Install httpx for LLM support")
            return None

        request_messages = self._prepare_messages_for_request(messages)
        body: dict[str, Any] = {
            "model": self._config.fast_model,
            "messages": request_messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": self._config.temperature,
        }
        if self._is_mimo_model:
            body["max_completion_tokens"] = self._config.max_tokens
        else:
            body["max_tokens"] = self._config.max_tokens

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        logger.info(log_name, model=self._config.fast_model, api_base=self._api_base, **(log_context or {}))
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

    @staticmethod
    def _extract_message(data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", [])
        if not choices:
            return {}
        return choices[0].get("message", {})

    @staticmethod
    def _prepare_messages_for_request(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return deepcopy(messages)
