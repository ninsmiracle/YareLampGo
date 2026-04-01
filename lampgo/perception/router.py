"""IntentRouter — classifies incoming requests into fast/slow paths.

Routing strategy:
  1. Keyword match (zero latency) — greetings, known skill keywords
  2. Fast LLM fallback (optional, ~500ms) — gpt-4o-mini function calling
  3. Fallback to COMPLEX intent — deferred to OpenClaw
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
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
    source: str = ""
    detail: str | None = None
    matched_keyword: str | None = None


NORMALIZE_TABLE = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "；": ";",
        "：": ":",
        "！": "!",
        "？": "?",
        "（": "(",
        "）": ")",
    }
)
TRAILING_PUNCTUATION = " ,.?!;:()[]{}\"'`"
COMPOSITE_MARKERS = ("然后", "再", "并且", "接着", "随后", "同时", "之后", ",", ";", "、")
CREATIVE_MARKERS = ("创作", "设计", "编排", "自定义", "原创", "即兴", "生成", "写一个", "做一个新的", "新动作", "新舞")
SCREEN_MARKERS = ("浏览器", "网页", "网站", "截图", "屏幕", "窗口", "打开", "保存文件", "下载", "登录", "表格", "邮箱")
PHYSICAL_MARKERS = ("台灯", "lampgo", "机械臂", "灯光", "打光", "表情", "动作", "点头", "摇头", "跳舞", "看桌面", "摄像头", "麦克风")
EXTERNAL_KNOWLEDGE_MARKERS = ("搜索", "查一下", "查找", "对比", "总结", "写代码", "代码", "工作流", "自动化", "cron", "日程")
ESCALATION_THRESHOLD = 5
GREETING_PHRASES = {
    "你好",
    "hi",
    "hello",
    "hey",
    "嗨",
    "早",
    "早上好",
    "下午好",
    "晚上好",
    "morning",
    "afternoon",
    "evening",
}
SKILL_KEYWORDS: dict[str, tuple[str, dict[str, Any] | None]] = {
    "点头": ("nod", None),
    "摇头": ("headshake", None),
    "跳舞": ("dance", None),
    "打招呼": ("nod", None),
    "停": ("estop", None),
    "停止": ("estop", None),
    "stop": ("estop", None),
    "回去": ("return_safe", None),
    "回家": ("return_safe", None),
    "home": ("return_safe", None),
    "nod": ("nod", None),
    "dance": ("dance", None),
    "shake": ("headshake", None),
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

    @property
    def has_llm_client(self) -> bool:
        return self._llm_client is not None

    def route(self, text: str) -> RoutedIntent:
        """Synchronous keyword-only routing."""
        return self._keyword_route(text)

    async def aroute(
        self,
        text: str,
        on_progress: Callable[[str, str, str], Awaitable[None]] | None = None,
    ) -> RoutedIntent:
        """Async routing: keyword first, then agent loop fallback if available."""
        result = self._keyword_route(text)
        if result.intent_type != IntentType.COMPLEX:
            return result

        logger.info("router.no_route_match", text=text)
        return result

    def _keyword_route(self, text: str) -> RoutedIntent:
        normalized = _normalize_text(text)
        # Backward-compatible: any composite/multi-step structure skips the keyword fast path.
        if _looks_composite(normalized):
            logger.info("router.keyword_skipped_composite", text=text, normalized=normalized)
            return RoutedIntent(
                intent_type=IntentType.COMPLEX,
                source="keyword",
                detail="包含复合结构，跳过关键词快路径",
            )

        if normalized in GREETING_PHRASES:
            logger.info("router.keyword_greeting_hit", text=text, normalized=normalized)
            return RoutedIntent(
                intent_type=IntentType.CHAT,
                chat_response=self._greeting_response(text),
                source="keyword",
                detail="问候语整句命中",
                matched_keyword=normalized,
            )

        if normalized in SKILL_KEYWORDS:
            skill_id, params = SKILL_KEYWORDS[normalized]
            logger.info("router.keyword_skill_hit", text=text, normalized=normalized, keyword=normalized, skill_id=skill_id)
            return RoutedIntent(
                intent_type=IntentType.SKILL,
                skill_id=skill_id,
                params=params,
                chat_response=f"好的，执行 {skill_id}",
                source="keyword",
                detail=f"关键词整句命中: {normalized}",
                matched_keyword=normalized,
            )

        logger.info("router.keyword_no_match", text=text, normalized=normalized)
        return RoutedIntent(
            intent_type=IntentType.COMPLEX,
            source="keyword",
            detail="未命中任何关键词",
        )

    async def run_agent_loop(
        self,
        text: str,
        execute_tool: Callable[[str, dict[str, Any], int, int], Awaitable[dict[str, Any]]],
        on_progress: Callable[[str, str, str], Awaitable[None]] | None = None,
        joint_state: dict[str, float] | None = None,
    ):
        if self._llm_client is None:
            raise RuntimeError("LLM client not configured")
        return await self._llm_client.run_agent_loop(
            text, execute_tool=execute_tool, on_progress=on_progress, joint_state=joint_state,
        )

    def _greeting_response(self, text: str) -> str:
        if any(w in text for w in ("早", "morning")):
            return "早上好！"
        if any(w in text for w in ("晚", "evening")):
            return "晚上好！"
        return "你好！"


def _normalize_text(text: str) -> str:
    normalized = text.strip().lower().translate(NORMALIZE_TABLE)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.rstrip(TRAILING_PUNCTUATION)
    return normalized


def _looks_composite(normalized: str) -> bool:
    return any(marker in normalized for marker in COMPOSITE_MARKERS)


def _complexity_score(text: str, normalized: str) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if _looks_composite(normalized):
        score += 2
        reasons.append("multi_step")

    if any(marker in text for marker in CREATIVE_MARKERS):
        score += 3
        reasons.append("creative")

    has_screen = any(marker in text for marker in SCREEN_MARKERS)
    has_physical = any(marker in text.lower() for marker in PHYSICAL_MARKERS)
    if has_screen and has_physical:
        score += 4
        reasons.append("cross_domain")

    if any(marker in text for marker in EXTERNAL_KNOWLEDGE_MARKERS):
        score += 3
        reasons.append("external_knowledge")

    if len(text) >= 50:
        score += 1
        reasons.append("long_input")

    return score, reasons
