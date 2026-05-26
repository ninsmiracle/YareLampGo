from __future__ import annotations

from lampgo.voice.agent_sdk import _SITECUSTOMIZE_CODE


def test_livekit_agent_session_patch_enables_interruptions_by_default() -> None:
    assert 'LAMPGO_LIVEKIT_ALLOW_INTERRUPTIONS' in _SITECUSTOMIZE_CODE
    assert 'kwargs.setdefault("allow_interruptions", _LAMPGO_ALLOW_INTERRUPTIONS)' in _SITECUSTOMIZE_CODE
    assert 'kwargs.setdefault("min_interruption_words", 3)' in _SITECUSTOMIZE_CODE
    assert 'kwargs.setdefault("allow_interruptions", True)' not in _SITECUSTOMIZE_CODE
