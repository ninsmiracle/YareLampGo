"""Lightweight async LLM client for fast intent classification.

Uses OpenAI-compatible function-calling API. Provider-agnostic via api_base.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from lampgo.core.config import LLMConfig
from lampgo.perception.router import IntentType, RoutedIntent

logger = structlog.get_logger(__name__)

MIMO_WEB_SEARCH_MODELS = {"mimo-v2-pro", "mimo-v2-omni", "mimo-v2-flash"}
WEB_SEARCH_HINTS = re.compile(
    r"(天气|气温|下雨|降雨|台风|空气质量|AQI|新闻|热搜|最新|最近|实时|股价|汇率|比分|路况|航班|日期|几号|星期几|几点|时间)",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You are lampgo, a smart desk lamp robot. Given a user's natural language input,
decide whether to invoke a robot skill, reply with short chat text, or hand off to the complex path.

Rules:
- For robot motion, recorded actions, and LED expressions, call the appropriate skill tool
- For greetings, normal conversation, and general factual questions, use chat_reply
- Use __complex__ only for tasks that require long multi-step reasoning, external app integrations, or capabilities lampgo does not have
- Always respond in the same language as the user
- Keep chat replies concise and helpful"""


def _build_tools_from_skills(skills: list[dict], config: LLMConfig) -> list[dict]:
    """Convert skill registry entries to tool specs for OpenAI-compatible APIs."""
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

        tools.append(
            {
                "type": "function",
                "function": {
                    "name": skill["skill_id"],
                    "description": skill["description"],
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            }
        )

    tools.append(
        {
            "type": "function",
            "function": {
                "name": "chat_reply",
                "description": "Reply to the user with a short chat message (no skill needed)",
                "parameters": {
                    "type": "object",
                    "properties": {"message": {"type": "string", "description": "Reply text"}},
                    "required": ["message"],
                },
            },
        }
    )
    tools.append(
        {
            "type": "function",
            "function": {
                "name": "__complex__",
                "description": "Hand off to the slow path for tasks that exceed lampgo's local capabilities",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Short reason why this needs the complex path",
                        }
                    },
                    "required": [],
                },
            },
        }
    )
    return tools


def _supports_mimo_web_search(config: LLMConfig) -> bool:
    return config.web_search_enabled and config.fast_model.strip().lower() in MIMO_WEB_SEARCH_MODELS


def _build_mimo_web_search_tool(config: LLMConfig) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "type": "web_search",
        "max_keyword": config.web_search_max_keyword,
        "force_search": config.web_search_force,
        "limit": config.web_search_limit,
    }
    location = {
        "type": "approximate",
        "country": config.web_search_country,
        "region": config.web_search_region,
        "city": config.web_search_city,
    }
    if any((config.web_search_country, config.web_search_region, config.web_search_city)):
        tool["user_location"] = location
    return tool


class LLMClient:
    """Async LLM client for intent classification via function calling."""

    def __init__(self, config: LLMConfig, skill_specs: list[dict]) -> None:
        self._config = config
        self._tools = _build_tools_from_skills(skill_specs, config)
        self._api_base = config.api_base or "https://api.openai.com/v1"
        self._is_mimo_model = config.fast_model.strip().lower() in MIMO_WEB_SEARCH_MODELS

    async def classify_intent(self, text: str) -> RoutedIntent | None:
        """Call fast LLM with function calling to classify user intent."""
        if not self._config.api_key:
            return None

        try:
            import httpx
        except ImportError:
            logger.warning("llm_client.no_httpx", msg="Install httpx for LLM support")
            return None

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]

        body = {
            "model": self._config.fast_model,
            "messages": messages,
            "tools": self._tools,
            "tool_choice": "required",
            "temperature": self._config.temperature,
        }
        if self._is_mimo_model:
            body["max_completion_tokens"] = self._config.max_tokens
            body["thinking"] = {"type": "disabled"}
        else:
            body["max_tokens"] = self._config.max_tokens

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
                resp = await client.post(f"{self._api_base}/chat/completions", json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "llm_client.request_failed",
                status_code=exc.response.status_code,
                response_text=exc.response.text[:1000],
            )
            if self._should_use_web_search(text):
                return await self._answer_with_mimo_web_search(text)
            return None
        except Exception:
            logger.exception("llm_client.request_failed", timeout_s=self._config.timeout_s)
            if self._should_use_web_search(text):
                return await self._answer_with_mimo_web_search(text)
            return None

        result = self._parse_response(data)
        if self._should_use_web_search(text) and (result is None or result.intent_type != IntentType.SKILL):
            search_result = await self._answer_with_mimo_web_search(text)
            if search_result:
                return search_result
        return result

    async def _answer_with_mimo_web_search(self, text: str) -> RoutedIntent | None:
        """Use MiMo's built-in web search for real-time factual questions."""
        if not _supports_mimo_web_search(self._config):
            return None

        try:
            import httpx
        except ImportError:
            logger.warning("llm_client.no_httpx", msg="Install httpx for LLM support")
            return None

        body = {
            "model": self._config.fast_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are lampgo, a smart desk lamp robot. "
                        "Use web search when needed and answer in the user's language. "
                        "Be concise but include the key facts."
                    ),
                },
                {"role": "user", "content": text},
            ],
            "tools": [_build_mimo_web_search_tool(self._config)],
            "tool_choice": "auto",
            "temperature": self._config.temperature,
            "max_completion_tokens": self._config.max_tokens,
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_s) as client:
                resp = await client.post(f"{self._api_base}/chat/completions", json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.exception(
                "llm_client.web_search_failed",
                status_code=exc.response.status_code,
                response_text=exc.response.text[:1000],
            )
            return None
        except Exception:
            logger.exception("llm_client.web_search_failed", timeout_s=self._config.timeout_s)
            return None

        choices = data.get("choices", [])
        if not choices:
            return None
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            return None
        return RoutedIntent(intent_type=IntentType.CHAT, chat_response=content)

    @staticmethod
    def _should_use_web_search(text: str) -> bool:
        return bool(WEB_SEARCH_HINTS.search(text))

    def _parse_response(self, data: dict) -> RoutedIntent | None:
        choices = data.get("choices", [])
        if not choices:
            return None

        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls", [])
        if not tool_calls:
            content = message.get("content", "")
            if content:
                return RoutedIntent(intent_type=IntentType.CHAT, chat_response=content)
            return None

        call = tool_calls[0]
        fn_name = call.get("function", {}).get("name", "")
        try:
            fn_args = json.loads(call.get("function", {}).get("arguments", "{}"))
        except json.JSONDecodeError:
            fn_args = {}

        if fn_name == "chat_reply":
            return RoutedIntent(
                intent_type=IntentType.CHAT,
                chat_response=fn_args.get("message", "你好！"),
            )

        if fn_name == "__complex__":
            return RoutedIntent(intent_type=IntentType.COMPLEX)

        return RoutedIntent(
            intent_type=IntentType.SKILL,
            skill_id=fn_name,
            params=fn_args if fn_args else None,
            chat_response=f"好的，执行 {fn_name}",
        )
