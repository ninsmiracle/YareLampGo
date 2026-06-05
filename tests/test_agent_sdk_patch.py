from __future__ import annotations

from lampgo.core.config import VoiceConfig
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
    cfg = VoiceConfig(
        livekit_url="https://rtc.yhaox.top",
        volcengine_app_id="app",
        volcengine_access_token="token",
    )
    manager = agent_sdk.AgentSDKManager(cfg)

    assert manager._can_start()
    assert checked == [agent_sdk.AGENT_SDK_MODULE]


def test_agent_sdk_can_start_reports_missing_lampgo_sdk(monkeypatch) -> None:
    monkeypatch.setattr(agent_sdk.importlib.util, "find_spec", lambda _name: None)
    cfg = VoiceConfig(
        livekit_url="https://rtc.yhaox.top",
        volcengine_app_id="app",
        volcengine_access_token="token",
    )
    manager = agent_sdk.AgentSDKManager(cfg)

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

    manager = agent_sdk.AgentSDKManager(VoiceConfig())

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

    manager = agent_sdk.AgentSDKManager(VoiceConfig())

    assert manager._resolve_sdk_binary() == str(path_cli)
