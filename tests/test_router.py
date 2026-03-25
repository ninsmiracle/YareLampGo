"""Tests for IntentRouter."""

from lampgo.perception.router import IntentRouter, IntentType


def test_greeting_routed_to_chat():
    router = IntentRouter()
    result = router.route("你好")
    assert result.intent_type == IntentType.CHAT
    assert result.chat_response is not None


def test_english_greeting():
    router = IntentRouter()
    result = router.route("Hello there")
    assert result.intent_type == IntentType.CHAT


def test_skill_keyword():
    router = IntentRouter()
    result = router.route("帮我点头")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "nod"


def test_dance_keyword():
    router = IntentRouter()
    result = router.route("dance for me")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "dance"


def test_stop_keyword():
    router = IntentRouter()
    result = router.route("stop immediately")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "estop"


def test_complex_fallback():
    router = IntentRouter()
    result = router.route("帮我分析一下这个代码的性能问题")
    assert result.intent_type == IntentType.COMPLEX


def test_morning_greeting():
    router = IntentRouter()
    result = router.route("早上好")
    assert result.intent_type == IntentType.CHAT
    assert "早" in result.chat_response
