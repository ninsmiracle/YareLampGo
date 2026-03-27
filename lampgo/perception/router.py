"""IntentRouter — classifies incoming requests into fast/slow paths.

Routing strategy:
  1. Keyword match (zero latency) — greetings, known skill keywords
  2. Fast LLM fallback (optional, ~500ms) — gpt-4o-mini function calling
  3. Fallback to COMPLEX intent — deferred to OpenClaw
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class IntentType(Enum):
    CHAT = "chat"
    SKILL = "skill"
    COMPLEX = "complex"


@dataclass
class RoutedIntent:
    intent_type: IntentType
    skill_id: str | None = None
    params: dict[str, Any] | None = None
    chat_response: str | None = None


GREETING_PATTERNS = re.compile(
    r"^(你好|hi|hello|hey|嗨|早|晚上好|下午好|早上好|morning|afternoon|evening)\b",
    re.IGNORECASE,
)

SKILL_KEYWORDS: dict[str, tuple[str, dict[str, Any] | None]] = {
    "点头": ("nod", None),
    "摇头": ("headshake", None),
    "跳舞": ("dance", None),
    "舞": ("dance", None),
    "看": ("look_at", None),
    "打招呼": ("nod", None),
    "停": ("estop", None),
    "stop": ("estop", None),
    "回去": ("return_safe", None),
    "回家": ("return_safe", None),
    "home": ("return_safe", None),
    "nod": ("nod", None),
    "dance": ("dance", None),
    "shake": ("headshake", None),
    "wave": ("dance", None),
    "idle": ("idle_sway", None),
    "sway": ("idle_sway", None),
    "害羞": ("set_expression", {"expression": "blush"}),
    "开心": ("set_expression", {"expression": "smiley"}),
    "难过": ("set_expression", {"expression": "crying"}),
    "生气": ("set_expression", {"expression": "angry"}),
    "惊讶": ("set_expression", {"expression": "surprised"}),
    "思考": ("set_expression", {"expression": "thinking"}),
    "爱心": ("set_expression", {"expression": "heart"}),
    "睡觉": ("set_expression", {"expression": "sleep"}),
    "彩虹": ("set_expression", {"expression": "rainbow"}),
}


class IntentRouter:
    """Fast intent classifier with optional LLM fallback.

    Phase 1: keyword-only matching (synchronous).
    Phase 2: add LLM fallback via set_llm_client().
    """

    def __init__(self) -> None:
        self._llm_client: Any = None

    def set_llm_client(self, client: Any) -> None:
        """Inject an LLMClient for fallback classification."""
        self._llm_client = client

    def route(self, text: str) -> RoutedIntent:
        """Synchronous keyword-only routing."""
        return self._keyword_route(text)

    async def aroute(self, text: str) -> RoutedIntent:
        """Async routing: keyword first, then LLM fallback if available."""
        result = self._keyword_route(text)
        if result.intent_type != IntentType.COMPLEX:
            return result

        if self._llm_client is not None:
            try:
                return await self._llm_route(text)
            except Exception:
                logger.exception("router.llm_fallback_error")

        return result

    def _keyword_route(self, text: str) -> RoutedIntent:
        text = text.strip()

        if GREETING_PATTERNS.match(text):
            return RoutedIntent(
                intent_type=IntentType.CHAT,
                chat_response=self._greeting_response(text),
            )

        text_lower = text.lower()
        for keyword, (skill_id, params) in SKILL_KEYWORDS.items():
            if keyword in text_lower:
                return RoutedIntent(
                    intent_type=IntentType.SKILL,
                    skill_id=skill_id,
                    params=params,
                    chat_response=f"好的，执行 {skill_id}",
                )

        return RoutedIntent(intent_type=IntentType.COMPLEX)

    async def _llm_route(self, text: str) -> RoutedIntent:
        """Use fast LLM to classify intent. Injected client must have classify_intent()."""
        result = await self._llm_client.classify_intent(text)
        if result is None:
            return RoutedIntent(intent_type=IntentType.COMPLEX)
        return result

    def _greeting_response(self, text: str) -> str:
        if any(w in text for w in ("早", "morning")):
            return "早上好！"
        if any(w in text for w in ("晚", "evening")):
            return "晚上好！"
        return "你好！"
