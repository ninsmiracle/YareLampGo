"""Text-level self-echo filtering for voice calls.

This is deliberately lightweight: it catches short ASR snippets that are very
close to text LampGo just spoke, without running DSP/AEC on the ESP32.
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from typing import Any

RECENT_TTS_WINDOW_S = 12.0
RECENT_TTS_MAX_ITEMS = 24


def normalise_echo_text(text: str) -> str:
    text = text.lower().replace(" ", "")
    return re.sub(r"[\s,，.。!！?？~～、:：;；\"'“”‘’（）()《》<>…—-]+", "", text)


def remember_tts_text(server: Any, text: str) -> None:
    cleaned = str(text or "").strip()
    if not cleaned:
        return
    now = time.monotonic()
    recent: list[tuple[float, str]] = getattr(server, "_livekit_recent_tts_texts", [])
    recent.append((now, cleaned))
    cutoff = now - RECENT_TTS_WINDOW_S
    server._livekit_recent_tts_texts = [  # type: ignore[attr-defined]
        item for item in recent[-RECENT_TTS_MAX_ITEMS:] if item[0] >= cutoff
    ]


def likely_recent_tts_echo(server: Any, user_text: str) -> tuple[bool, dict[str, Any]]:
    cfg = getattr(server, "config", None)
    voice = getattr(cfg, "voice", None)
    mode = str(getattr(voice, "call_mode", "stable") or "stable").strip().lower().replace("-", "_")
    mode = {
        "safe": "stable",
        "half_duplex": "stable",
        "interrupt": "interruptible",
        "interruptions": "interruptible",
        "barge_in": "interruptible",
        "aec": "esp32_aec",
        "experimental_aec": "esp32_aec",
    }.get(mode, mode)
    if mode not in {"interruptible", "esp32_aec"}:
        return False, {"mode": mode, "reason": "mode_not_interruptible"}
    if not bool(getattr(voice, "echo_text_filter_enabled", True)):
        return False, {"mode": mode, "reason": "filter_disabled"}

    user_norm = normalise_echo_text(user_text)
    if len(user_norm) < 4:
        return False, {"mode": mode, "reason": "too_short", "normalized_len": len(user_norm)}

    now = time.monotonic()
    recent: list[tuple[float, str]] = getattr(server, "_livekit_recent_tts_texts", [])
    best_ratio = 0.0
    best_age = None
    best_text = ""
    cutoff = now - RECENT_TTS_WINDOW_S
    kept: list[tuple[float, str]] = []
    for ts, tts_text in recent[-RECENT_TTS_MAX_ITEMS:]:
        if ts < cutoff:
            continue
        kept.append((ts, tts_text))
        tts_norm = normalise_echo_text(tts_text)
        if len(tts_norm) < 4:
            continue
        ratio = SequenceMatcher(None, user_norm, tts_norm).ratio()
        age_s = now - ts
        if ratio > best_ratio:
            best_ratio = ratio
            best_age = age_s
            best_text = tts_text

        is_contained = user_norm in tts_norm or tts_norm in user_norm
        # Chinese ASR echo snippets are often only 4-5 chars, e.g. "你打算怎么"
        # from a spoken "你打算怎么办".  Let them through only when the match is
        # extremely close, so normal short user turns are not broadly suppressed.
        if len(user_norm) < 6:
            if is_contained or ratio >= 0.9:
                server._livekit_recent_tts_texts = kept  # type: ignore[attr-defined]
                return True, {
                    "mode": mode,
                    "ratio": round(ratio, 3),
                    "age_s": round(age_s, 2),
                    "short_text": True,
                }
            continue

        if is_contained or ratio >= 0.82:
            server._livekit_recent_tts_texts = kept  # type: ignore[attr-defined]
            return True, {"mode": mode, "ratio": round(ratio, 3), "age_s": round(age_s, 2)}
        if ratio >= 0.65 and age_s <= 2.5:
            server._livekit_recent_tts_texts = kept  # type: ignore[attr-defined]
            return True, {"mode": mode, "ratio": round(ratio, 3), "age_s": round(age_s, 2)}

    server._livekit_recent_tts_texts = kept  # type: ignore[attr-defined]
    return False, {
        "mode": mode,
        "ratio": round(best_ratio, 3),
        "age_s": round(best_age, 2) if best_age is not None else None,
        "candidate": best_text[:60],
    }
