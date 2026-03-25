"""Tests for the upgraded IntentRouter (keyword + async aroute)."""

from __future__ import annotations

import pytest

from lampgo.perception.router import IntentRouter, IntentType, RoutedIntent


@pytest.fixture
def router():
    return IntentRouter()


def test_keyword_greeting(router):
    result = router.route("你好")
    assert result.intent_type == IntentType.CHAT
    assert result.chat_response is not None


def test_keyword_skill(router):
    result = router.route("跳舞")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "dance"


def test_keyword_expression(router):
    result = router.route("做个害羞的表情")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "set_expression"
    assert result.params == {"mode": "blush"}


def test_keyword_complex_fallback(router):
    result = router.route("帮我把今天的PPT发给老板")
    assert result.intent_type == IntentType.COMPLEX


@pytest.mark.asyncio
async def test_aroute_keyword_hit(router):
    result = await router.aroute("点头")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "nod"


@pytest.mark.asyncio
async def test_aroute_no_llm_fallback(router):
    """Without LLM client, complex intents stay complex."""
    result = await router.aroute("帮我把灯抬高一点")
    assert result.intent_type == IntentType.COMPLEX


@pytest.mark.asyncio
async def test_aroute_with_mock_llm(router):
    """With a mock LLM client, complex intents get classified."""

    class MockLLM:
        async def classify_intent(self, text: str) -> RoutedIntent:
            return RoutedIntent(
                intent_type=IntentType.SKILL,
                skill_id="move_to",
                params={"base_pitch": -20},
                chat_response="好的，抬高灯头",
            )

    router.set_llm_client(MockLLM())
    result = await router.aroute("帮我把灯抬高一点")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "move_to"


def test_morning_greeting(router):
    result = router.route("早上好")
    assert result.intent_type == IntentType.CHAT
    assert "早" in result.chat_response


def test_estop_keyword(router):
    result = router.route("停")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "estop"


def test_return_safe_keyword(router):
    result = router.route("回家")
    assert result.intent_type == IntentType.SKILL
    assert result.skill_id == "return_safe"
