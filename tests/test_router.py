"""Tests for IntentRouter."""

from lampgo.perception.router import IntentRouter, IntentType


def test_greeting_routed_to_chat():
    router = IntentRouter()
    result = router.route("你好")
    assert result.intent_type == IntentType.CHAT
    assert result.chat_response is not None


def test_english_greeting():
    router = IntentRouter()
    result = router.route("hello")
    assert result.intent_type == IntentType.CHAT


def test_skill_keyword():
    router = IntentRouter()
    result = router.route("点头")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "nod"
    assert result.source == "keyword"
    assert result.matched_keyword == "点头"


def test_dance_keyword():
    router = IntentRouter()
    result = router.route("dance")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "dance"


def test_stop_keyword():
    router = IntentRouter()
    result = router.route("stop")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "estop"


def test_complex_fallback():
    router = IntentRouter()
    result = router.route("帮我分析一下这个代码的性能问题")
    assert result.intent_type == IntentType.COMPLEX
    assert result.source == "keyword"


def test_morning_greeting():
    router = IntentRouter()
    result = router.route("早上好")
    assert result.intent_type == IntentType.CHAT
    assert "早" in result.chat_response


def test_composite_sentence_skips_keyword_fast_path():
    router = IntentRouter()
    result = router.route("跳个舞，唱个歌")
    assert result.intent_type == IntentType.COMPLEX
    assert result.detail == "包含复合结构，跳过关键词快路径"
