from __future__ import annotations

from lampgo.perception.router import IntentRouter, IntentType


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
