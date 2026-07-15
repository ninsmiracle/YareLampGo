from __future__ import annotations

import pytest

from lampgo.perception.router import IntentRouter, IntentType


@pytest.mark.parametrize(
    "text",
    [
        "把 Codex 叫来，帮我分析这个项目",
        "把你大哥叫来处理一下",
        "这个活交给 Codex",
        "让你大哥来接手",
        "调用一下 Codex 帮我改代码",
    ],
)
def test_explicit_codex_summon_routes_directly_to_agent(text: str) -> None:
    intent = IntentRouter().route(text)

    assert intent.intent_type is IntentType.COMPLEX
    assert intent.direct_agent is True
    assert intent.detail == "用户明确点名调用本机 Codex"


@pytest.mark.parametrize(
    "text",
    [
        "Codex 是什么",
        "我大哥今天来了",
        "我大哥来找我了，顺便帮我改了文档",
        "先别把 Codex 叫来",
    ],
)
def test_codex_mention_without_summon_does_not_force_direct_agent(text: str) -> None:
    intent = IntentRouter().route(text)

    assert intent.direct_agent is False


def test_music_style_keyword_routes_to_matching_preset():
    intent = IntentRouter().route("摇滚音乐律动")

    assert intent.intent_type is IntentType.SKILL
    assert intent.skill_id == "dance_to_music"
    assert intent.params == {"style": "rock"}


def test_dj_music_keyword_normalizes_case():
    intent = IntentRouter().route("DJ 音乐律动")

    assert intent.intent_type is IntentType.SKILL
    assert intent.skill_id == "dance_to_music"
    assert intent.params == {"style": "dj"}
