"""Lightweight async LLM client for fast intent classification.

Uses OpenAI-compatible function-calling API. Provider-agnostic via api_base.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from lampgo.core.config import LLMConfig
from lampgo.perception.router import IntentType, RoutedIntent

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are lampgo, a smart desk lamp robot. Given a user's natural language input, 
decide which skill to invoke OR provide a short chat reply.

Rules:
- For simple greetings, reply directly (no skill needed)
- For motion requests, map to the appropriate skill with parameters
- For expressions/emotions, use set_expression with the right mode
- For unknown/complex requests, return skill_id="__complex__"
- Always respond in the same language as the user
- Keep chat replies under 30 characters"""


def _build_tools_from_skills(skills: list[dict]) -> list[dict]:
    """Convert skill registry entries to OpenAI function-calling tool specs."""
    tools = []
    for skill in skills:
        props: dict[str, Any] = {}
        required: list[str] = []
        for pname, pspec in skill.get("parameters", {}).items():
            ptype = pspec.get("type", "string")
            json_type = {"float": "number", "int": "integer", "str": "string", "bool": "boolean"}.get(ptype, "string")
            props[pname] = {"type": json_type, "description": pspec.get("description", "")}
            if pspec.get("required", False):
                required.append(pname)

        tools.append({
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
        })

    tools.append({
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
    })
    return tools


class LLMClient:
    """Async LLM client for intent classification via function calling."""

    def __init__(self, config: LLMConfig, skill_specs: list[dict]) -> None:
        self._config = config
        self._tools = _build_tools_from_skills(skill_specs)
        self._api_base = config.api_base or "https://api.openai.com/v1"

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
            "max_tokens": self._config.max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"{self._api_base}/chat/completions", json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("llm_client.request_failed")
            return None

        return self._parse_response(data)

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
