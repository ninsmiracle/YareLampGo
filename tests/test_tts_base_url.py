"""Regression tests for the Volcengine/Edge TTS provider selection."""

from __future__ import annotations

import inspect

from lampgo.core.config import VoiceConfig
from lampgo.voice.agent_sdk import AgentSDKManager
from lampgo.voice.tts import (
    DEFAULT_VOLCENGINE_TTS_VOICE,
    VOLCENGINE_BIGTTS_RESOURCE_ID,
    VOLCENGINE_SEED_TTS_1_RESOURCE_ID,
    VOLCENGINE_SEED_TTS_2_RESOURCE_ID,
    VOLCENGINE_TTS_ENDPOINT,
    VolcengineTTS,
    _resource_id_for_voice,
    _volcengine_voice_or_default,
    synthesize_for_web,
)


def test_volcengine_tts_defaults_to_v3_bidirectional_endpoint() -> None:
    assert VOLCENGINE_TTS_ENDPOINT == "wss://openspeech.bytedance.com/api/v3/tts/bidirection"


def test_volcengine_tts_default_voice_is_uranus_bigtts() -> None:
    tts = VolcengineTTS(app_id="app", access_token="token")
    assert tts._voice == DEFAULT_VOLCENGINE_TTS_VOICE == "zh_female_vv_uranus_bigtts"


def test_volcengine_tts_resource_id_matches_voice_family() -> None:
    assert _resource_id_for_voice(DEFAULT_VOLCENGINE_TTS_VOICE) == VOLCENGINE_SEED_TTS_2_RESOURCE_ID
    assert _resource_id_for_voice("saturn_zh_female_qingyingduoduo_cs_tob") == VOLCENGINE_SEED_TTS_2_RESOURCE_ID
    assert _resource_id_for_voice("zh_female_shuangkuaisisi_moon_bigtts") == VOLCENGINE_SEED_TTS_1_RESOURCE_ID
    assert _resource_id_for_voice("zh_female_roumeinvyou_emo_v2_mars_bigtts") == VOLCENGINE_BIGTTS_RESOURCE_ID


def test_volcengine_tts_normalizes_legacy_mimo_voice() -> None:
    tts = VolcengineTTS(app_id="app", access_token="token", voice="mimo_default")
    assert tts._voice == DEFAULT_VOLCENGINE_TTS_VOICE


def test_volcengine_tts_normalizes_granted_voice_aliases() -> None:
    assert _volcengine_voice_or_default("zh_male_lubanqihao_mars_bigtts") == "zh_male_lubanqihao_uranus_bigtts"
    assert (
        _volcengine_voice_or_default("zh_male_dongmanhaimian_mars_bigtts")
        == "zh_male_liangsangmengzai_uranus_bigtts"
    )
    assert _volcengine_voice_or_default("zh_male_wennuanahu_moon_bigtts") == "zh_male_wennuanahu_uranus_bigtts"


def test_synthesize_for_web_uses_volcengine_credentials_not_llm_api_key() -> None:
    params = inspect.signature(synthesize_for_web).parameters
    assert "app_id" in params
    assert "access_token" in params
    assert "api_key" not in params


def test_voice_config_migrates_legacy_mimo_voice_defaults() -> None:
    cfg = VoiceConfig(
        stt_provider="mimo",
        stt_model="mimo-v2.5",
        tts_provider="mimo",
        tts_model="mimo-v2.5-tts",
        tts_voice="mimo_default",
        livekit_tts_voice="BV700_streaming",
    )
    assert cfg.stt_provider == "volcengine"
    assert cfg.stt_model == "bigmodel"
    assert cfg.tts_provider == "volcengine"
    assert cfg.tts_model == ""
    assert cfg.tts_voice == DEFAULT_VOLCENGINE_TTS_VOICE
    assert cfg.livekit_tts_voice == DEFAULT_VOLCENGINE_TTS_VOICE


def test_voice_config_migrates_incompatible_builtin_voice_ids() -> None:
    cfg = VoiceConfig(
        tts_voice="zh_male_dongmanhaimian_mars_bigtts",
        livekit_tts_voice="zh_male_wennuanahu_moon_bigtts",
    )
    assert cfg.tts_voice == "zh_male_liangsangmengzai_uranus_bigtts"
    assert cfg.livekit_tts_voice == "zh_male_wennuanahu_uranus_bigtts"


def test_agent_sdk_roles_yaml_uses_cloud_auth_and_frontend_tts_voice_for_livekit(monkeypatch) -> None:
    monkeypatch.delenv("LAMPGO_RTC_TOKEN_API_KEY", raising=False)
    monkeypatch.delenv("LAMPGO_AGENT_REGISTRATION_TOKEN", raising=False)
    cfg = VoiceConfig(
        livekit_url="https://rtc.yhaox.top",
        volcengine_app_id="app",
        volcengine_access_token="token",
        tts_voice="zh_male_liangsangmengzai_uranus_bigtts",
        livekit_tts_voice="zh_female_jitangnv_uranus_bigtts",
    )
    manager = AgentSDKManager(cfg)
    roles_path = manager._generate_roles_yaml()
    try:
        roles_yaml = roles_path.read_text(encoding="utf-8")
    finally:
        roles_path.unlink(missing_ok=True)

    assert 'url: "wss://rtc.yhaox.top"' in roles_yaml
    assert 'rtc_token_endpoint: "https://rtc.yhaox.top/rtc/token"' in roles_yaml
    assert 'agent_token_endpoint: "https://rtc.yhaox.top/agent/token"' in roles_yaml
    assert 'rtc_token_api_key: "livekit-token"' in roles_yaml
    assert 'registration_token: "livekit-token"' in roles_yaml
    assert "\n  api_key:" not in roles_yaml
    assert "\n  api_secret:" not in roles_yaml
    assert 'voice: "zh_male_liangsangmengzai_uranus_bigtts"' in roles_yaml
    assert "zh_female_jitangnv_uranus_bigtts" not in roles_yaml
