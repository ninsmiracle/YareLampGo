from __future__ import annotations

from lampgo.core.config import LLMConfig, VoiceConfig
from lampgo.voice import agent_sdk
from lampgo.voice.agent_sdk import _SITECUSTOMIZE_CODE


def test_livekit_agent_session_patch_enables_interruptions_by_default() -> None:
    assert 'LAMPGO_LIVEKIT_ALLOW_INTERRUPTIONS' in _SITECUSTOMIZE_CODE
    assert 'kwargs.setdefault("allow_interruptions", _LAMPGO_ALLOW_INTERRUPTIONS)' in _SITECUSTOMIZE_CODE
    assert 'kwargs.setdefault("min_interruption_words", 3)' in _SITECUSTOMIZE_CODE
    assert 'kwargs.setdefault("allow_interruptions", True)' not in _SITECUSTOMIZE_CODE


def test_agent_sdk_can_start_checks_lampgo_sdk_import(monkeypatch) -> None:
    checked: list[str] = []

    def fake_find_spec(name: str):
        checked.append(name)
        return object() if name == agent_sdk.AGENT_SDK_MODULE else None

    monkeypatch.setattr(agent_sdk.importlib.util, "find_spec", fake_find_spec)
    cfg = VoiceConfig(livekit_url="https://rtc.yhaox.top")
    manager = agent_sdk.AgentSDKManager(cfg, _mimo_llm())
    monkeypatch.setattr(manager, "_local_livekit_server_reachable", lambda: True)

    assert manager._can_start()
    assert checked == [agent_sdk.AGENT_SDK_MODULE]


def test_agent_sdk_can_start_reports_missing_lampgo_sdk(monkeypatch) -> None:
    monkeypatch.setattr(agent_sdk.importlib.util, "find_spec", lambda _name: None)
    cfg = VoiceConfig(livekit_url="https://rtc.yhaox.top")
    manager = agent_sdk.AgentSDKManager(cfg, _mimo_llm())
    monkeypatch.setattr(manager, "_local_livekit_server_reachable", lambda: True)

    assert not manager._can_start()
    assert agent_sdk.AGENT_SDK_PACKAGE in manager.last_error


def test_agent_sdk_binary_resolves_cli_in_current_env(monkeypatch, tmp_path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.touch()
    sdk_cli = bin_dir / "lampgo-livekit-agent"
    sdk_cli.touch()
    monkeypatch.setattr(agent_sdk.sys, "executable", str(python))
    monkeypatch.setattr(agent_sdk.shutil, "which", lambda _name: None)

    manager = agent_sdk.AgentSDKManager(VoiceConfig(), _mimo_llm())

    assert manager._resolve_sdk_binary() == str(sdk_cli)


def test_agent_sdk_binary_falls_back_to_path(monkeypatch, tmp_path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.touch()
    path_cli = tmp_path / "path" / "lampgo-livekit-agent"
    path_cli.parent.mkdir()
    path_cli.touch()
    monkeypatch.setattr(agent_sdk.sys, "executable", str(python))
    monkeypatch.setattr(
        agent_sdk.shutil,
        "which",
        lambda name: str(path_cli) if name == "lampgo-livekit-agent" else None,
    )

    manager = agent_sdk.AgentSDKManager(VoiceConfig(), _mimo_llm())

    assert manager._resolve_sdk_binary() == str(path_cli)


def test_sitecustomize_installs_mimo_factories_before_sdk_worker_import() -> None:
    assert "install_livekit_agent_sdk_mimo_patch" in _SITECUSTOMIZE_CODE
    assert "installed MiMo ASR/TTS adapter" in _SITECUSTOMIZE_CODE


def test_mimo_patch_replaces_worker_bound_factory_references() -> None:
    from lampgo_livekit_agent import config, speech, worker
    from lampgo.voice.mimo_livekit import create_stt, create_tts, install_livekit_agent_sdk_mimo_patch

    original = {
        "speech_stt": speech.create_stt,
        "speech_tts": speech.create_tts,
        "worker_stt": worker.create_stt,
        "worker_tts": worker.create_tts,
        "stt_types": set(config._SUPPORTED_COMPONENT_TYPES["stt"]),
        "tts_types": set(config._SUPPORTED_COMPONENT_TYPES["tts"]),
    }
    try:
        install_livekit_agent_sdk_mimo_patch()

        assert speech.create_stt is create_stt
        assert speech.create_tts is create_tts
        assert worker.create_stt is create_stt
        assert worker.create_tts is create_tts
    finally:
        speech.create_stt = original["speech_stt"]
        speech.create_tts = original["speech_tts"]
        worker.create_stt = original["worker_stt"]
        worker.create_tts = original["worker_tts"]
        config._SUPPORTED_COMPONENT_TYPES["stt"] = original["stt_types"]
        config._SUPPORTED_COMPONENT_TYPES["tts"] = original["tts_types"]


def test_mimo_adapter_emits_lifecycle_markers() -> None:
    source = __import__("inspect").getsource(__import__("lampgo.voice.mimo_livekit", fromlist=["*"]))

    assert "voice.mimo_livekit_asr_dispatch" in source
    assert "voice.mimo_livekit_asr_result" in source
    assert "voice.mimo_livekit_tts_first_audio" in source
    assert "voice.mimo_livekit_tts_flushed" in source


def _mimo_llm() -> LLMConfig:
    return LLMConfig(
        provider="mimo",
        api_base="https://api.xiaomimimo.com/v1",
        api_key="mimo-key",
    )
