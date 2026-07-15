"""IntentRouter — classifies incoming requests into fast/slow paths.

Routing strategy:
  1. Keyword match (zero latency) — greetings, known skill keywords
  2. Fast LLM fallback (optional, ~500ms) — gpt-4o-mini function calling
  3. Fallback to COMPLEX intent — deferred to the local agent harness
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
    end_conversation: bool = False
    direct_agent: bool = False


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
CODEX_SUMMON_PATTERNS = (
    re.compile(r"(?:codex|你大哥|大哥)(?:给我)?(?:叫|喊|请|找|拉|召唤|调用|启动|请出)(?:一下|下)?(?:来|过来)?"),
    re.compile(r"(?:叫|喊|请|找|拉|召唤|调用|启动|请出)(?:一下|下)?(?:codex|你大哥|大哥)(?:来|过来)?"),
    re.compile(r"(?:交给|转给)(?:codex|你大哥|大哥)"),
    re.compile(r"让(?:codex|你大哥|大哥)(?:来|过来|处理|接手|帮忙)"),
    re.compile(r"(?:codex|你大哥|大哥)(?:来|过来)(?:处理|接手|帮忙)?"),
)
CODEX_SUMMON_NEGATION_RE = re.compile(
    r"(?:不要|不用|无需|不必|先别|别)(?:把|叫|喊|请|找|拉|召唤|调用|启动|交给|转给)?(?:codex|你大哥|大哥)"
    r"|(?:codex|你大哥|大哥)(?:先)?(?:别|不要|不用)(?:来|过来|处理|接手)?"
)
INLINE_PUNCTUATION_RE = re.compile(r"[,.;:!?、]+")
LEADING_FILLER_RE = re.compile(
    r"^(?:(?:嗯+|呃+|额+|啊+|哦+|喔+|诶+|欸+|uh+|um+|erm+|mmm+|那个|就是)[,.;:!?、]*)+",
    re.IGNORECASE,
)
CREATIVE_MARKERS = (
    "创作", "设计", "编排", "自定义", "原创", "即兴", "生成", "写一个", "做一个新的", "新动作", "新舞",
)
SCREEN_MARKERS = (
    "浏览器", "网页", "网站", "截图", "屏幕", "窗口", "打开", "保存文件", "下载", "登录", "表格", "邮箱",
)
PHYSICAL_MARKERS = (
    "台灯", "lampgo", "机械臂", "灯光", "打光", "表情", "动作", "点头", "摇头", "跳舞", "看桌面", "摄像头", "麦克风",
)
EXTERNAL_KNOWLEDGE_MARKERS = (
    "搜索", "查一下", "查找", "对比", "总结", "写代码", "代码", "工作流", "自动化", "cron", "日程",
)
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
GOODBYE_PHRASES = {
    "再见",
    "拜拜",
    "bye",
    "goodbye",
    "晚安",
    "下次见",
    "回头见",
    "挂断",
    "结束通话",
    "退出",
    "先这样",
    "不聊了",
}
SKILL_KEYWORDS: dict[str, tuple[str, dict[str, Any] | None]] = {
    "点头": ("nod", None),
    "摇头": ("headshake", None),
    "音乐律动": ("dance_to_music", None),
    "爵士音乐律动": ("dance_to_music", {"style": "jazz"}),
    "电子音乐律动": ("dance_to_music", {"style": "electronic"}),
    "摇滚音乐律动": ("dance_to_music", {"style": "rock"}),
    "氛围音乐律动": ("dance_to_music", {"style": "ambient"}),
    "古风音乐律动": ("dance_to_music", {"style": "gufeng"}),
    "dj音乐律动": ("dance_to_music", {"style": "dj"}),
    "跟音乐跳舞": ("dance_to_music", None),
    "跟着电脑音乐跳舞": ("dance_to_music", None),
    "跟着音乐跳舞": ("dance_to_music", None),
    "随音乐跳舞": ("dance_to_music", None),
    "跳舞": ("play_recording", {"name": "dance1"}),
    "打招呼": ("nod", None),
    "停": ("estop", None),
    "停止": ("estop", None),
    "stop": ("estop", None),
    "回去": ("return_safe", None),
    "回家": ("return_safe", None),
    "home": ("return_safe", None),
    "nod": ("nod", None),
    "dance": ("play_recording", {"name": "dance1"}),
    "shake": ("headshake", None),
    "idle": ("idle_sway", None),
    "sway": ("idle_sway", None),
    "害羞": ("set_expression", {"expression": "blush"}),
    "开心": ("set_expression", {"expression": "smiley"}),
    "难过": ("set_expression", {"expression": "sad"}),
    "伤心": ("set_expression", {"expression": "sad"}),
    "生气": ("set_expression", {"expression": "angry"}),
    "惊讶": ("set_expression", {"expression": "surprised"}),
    "思考": ("set_expression", {"expression": "thinking"}),
    "爱心": ("set_expression", {"expression": "heart"}),
    "睡觉": ("set_expression", {"expression": "sleep"}),
    "无奈": ("set_expression", {"expression": "helpless"}),
    "耍酷": ("set_expression", {"expression": "cool"}),
    "专注": ("set_expression", {"expression": "focused"}),
    "眨眼": ("set_expression", {"expression": "wink"}),
    "彩虹": ("set_expression", {"expression": "rainbow"}),
    "yu7": ("set_expression", {"expression": "myu7gt"}),
    "yu7gt": ("set_expression", {"expression": "myu7gt"}),
    "myu7": ("set_expression", {"expression": "myu7gt"}),
    "mgt": ("set_expression", {"expression": "myu7gt"}),
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
        normalized = _strip_leading_fillers(_normalize_text(text))
        codex_summon = _match_codex_summon(normalized)
        if codex_summon:
            logger.info(
                "router.codex_summon_hit",
                text=text,
                normalized=normalized,
                keyword=codex_summon,
            )
            return RoutedIntent(
                intent_type=IntentType.COMPLEX,
                source="keyword",
                detail="用户明确点名调用本机 Codex",
                matched_keyword=codex_summon,
                direct_agent=True,
            )

        greeting_keyword = _match_greeting_phrase(normalized)
        if greeting_keyword:
            logger.info("router.keyword_greeting_hit", text=text, normalized=normalized, keyword=greeting_keyword)
            return RoutedIntent(
                intent_type=IntentType.CHAT,
                chat_response=self._greeting_response(text),
                source="keyword",
                detail="问候语命中",
                matched_keyword=greeting_keyword,
            )

        goodbye_keyword = _match_repeated_phrase(normalized, GOODBYE_PHRASES)
        if goodbye_keyword:
            logger.info("router.keyword_goodbye_hit", text=text, normalized=normalized, keyword=goodbye_keyword)
            return RoutedIntent(
                intent_type=IntentType.CHAT,
                chat_response="好啦，先这样～",
                source="keyword",
                detail="告别语命中",
                matched_keyword=goodbye_keyword,
                end_conversation=True,
            )

        if _looks_composite(normalized):
            logger.info("router.keyword_skipped_composite", text=text, normalized=normalized)
            return RoutedIntent(
                intent_type=IntentType.COMPLEX,
                source="keyword",
                detail="包含复合结构，跳过关键词快路径",
            )

        if normalized in SKILL_KEYWORDS:
            skill_id, params = SKILL_KEYWORDS[normalized]
            logger.info(
                "router.keyword_skill_hit",
                text=text,
                normalized=normalized,
                keyword=normalized,
                skill_id=skill_id,
            )
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
        audio_data: str | None = None,
        publish_tool_event: Callable[..., Awaitable[None]] | None = None,
        history: list[dict[str, Any]] | None = None,
        call_mode: bool = False,
        enable_thinking: bool = False,
    ):
        if self._llm_client is None:
            raise RuntimeError("LLM client not configured")
        return await self._llm_client.run_agent_loop(
            text,
            execute_tool=execute_tool,
            on_progress=on_progress,
            joint_state=joint_state,
            audio_data=audio_data,
            publish_tool_event=publish_tool_event,
            history=history,
            call_mode=call_mode,
            enable_thinking=enable_thinking,
        )

    async def transcribe_audio(self, audio_data: str) -> str:
        """Use omni model to transcribe audio to text (no tool calling)."""
        if self._llm_client is None:
            raise RuntimeError("LLM client not configured")
        return await self._llm_client.transcribe_audio(audio_data)

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


def _strip_leading_fillers(normalized: str) -> str:
    return LEADING_FILLER_RE.sub("", normalized)


def _match_greeting_phrase(normalized: str) -> str | None:
    return _match_repeated_phrase(normalized, GREETING_PHRASES)


def _match_codex_summon(normalized: str) -> str | None:
    """Match an explicit summon, not a casual mention of Codex or 大哥."""
    if CODEX_SUMMON_NEGATION_RE.search(normalized):
        return None
    for pattern in CODEX_SUMMON_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return match.group(0)
    return None


def _match_repeated_phrase(normalized: str, phrases: set[str]) -> str | None:
    compact = INLINE_PUNCTUATION_RE.sub("", normalized)
    if compact in phrases:
        return compact

    for phrase in sorted(phrases, key=len, reverse=True):
        remaining = compact
        matched = 0
        while remaining.startswith(phrase):
            remaining = remaining[len(phrase) :]
            matched += 1
        if matched and not remaining:
            return phrase
    return None


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
