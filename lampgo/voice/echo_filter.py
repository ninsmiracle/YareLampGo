"""Bounded text echo suppression for ASR, not acoustic echo cancellation."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

RECENT_TTS_WINDOW_S = 90.0  # hard cap; each utterance expires sooner when possible
RECENT_TTS_MAX_ITEMS = 24


@dataclass(frozen=True)
class _Reference:
    created: float
    expires: float
    text: str


def normalise_echo_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower(), flags=re.UNICODE)


def clear_recent_tts(server: Any) -> None:
    server._livekit_recent_tts_texts = []
    server._livekit_tts_expected_until = 0.0


def remember_tts_text(server: Any, text: str) -> None:
    cleaned = str(text or "").strip()
    if not cleaned:
        return
    now = time.monotonic()
    recent = [r for r in getattr(server, "_livekit_recent_tts_texts", []) if r.expires >= now]
    # Narration and the streaming transport both register the same utterance.
    if recent and now - recent[-1].created < 2 and normalise_echo_text(recent[-1].text) == normalise_echo_text(cleaned):
        return
    # Include synthesis startup, queued speech and delayed final ASR results.
    # 12 seconds from text generation expired before playback finished.
    start = max(now + 8.0, getattr(server, "_livekit_tts_expected_until", 0.0))
    end = min(now + 70.0, start + max(1.0, len(normalise_echo_text(cleaned)) / 4.0))
    server._livekit_tts_expected_until = end
    recent.append(_Reference(now, min(now + RECENT_TTS_WINDOW_S, max(now + 30.0, end + 15.0)), cleaned))
    server._livekit_recent_tts_texts = recent[-RECENT_TTS_MAX_ITEMS:]


def _similarity(fragment: str, reference: str) -> tuple[float, str]:
    """Compare an ASR fragment to the relevant window of a longer utterance."""
    if not fragment or not reference:
        return 0.0, ""
    if fragment in reference:
        return 1.0, fragment
    matcher = SequenceMatcher(None, fragment, reference, autojunk=False)
    best = matcher.ratio()
    best_window = reference
    # Alignment blocks locate a few plausible windows, without a quadratic
    # sliding-window scan. The whole ASR fragment must match, not just a prefix.
    for block in matcher.get_matching_blocks():
        if block.size < 3:
            continue
        for delta in (-2, 0, 2):
            start = max(0, block.b - block.a + delta)
            for extra in (-2, 0, 2):
                window = reference[start:start + max(1, len(fragment) + extra)]
                score = SequenceMatcher(None, fragment, window, autojunk=False).ratio()
                if score > best:
                    best, best_window = score, window
    return best, best_window


def filter_recent_tts_echo(server: Any, user_text: str) -> tuple[str, dict[str, Any]]:
    """Return empty for pure echo, or preserve new speech after echo clauses."""
    voice = getattr(getattr(server, "config", None), "voice", None)
    mode = str(getattr(voice, "call_mode", "stable") or "stable").lower().replace("-", "_")
    if not bool(getattr(voice, "echo_text_filter_enabled", True)):
        return user_text, {"mode": mode, "reason": "filter_disabled"}
    # Also catch playback-tail echo in stable mode after the mic gate reopens.
    now = time.monotonic()
    recent = [r for r in getattr(server, "_livekit_recent_tts_texts", []) if r.expires >= now]
    server._livekit_recent_tts_texts = recent  # prune once, never truncate at the first match
    candidates = [(r, normalise_echo_text(r.text)[:2000]) for r in recent]
    detail: dict[str, Any] = {"mode": mode, "ratio": 0.0, "age_s": None, "candidate": ""}

    def matches(text: str) -> bool:
        normalized = normalise_echo_text(text)
        if len(normalized) < 4 or len(normalized) > 512:
            return False
        for ref, normalized_ref in reversed(candidates):
            score, window = _similarity(normalized, normalized_ref)
            if score > detail["ratio"]:
                detail.update(ratio=round(score, 3), age_s=round(now - ref.created, 2), candidate=ref.text[:60])
            threshold = 1.0 if len(normalized) < 6 else 0.86
            # One extra negation or question particle can reverse the meaning
            # even when the rest repeats what the lamp said.
            novel_intent = any(normalized.count(cue) > window.count(cue) for cue in (
                "不", "没", "别", "停", "吗", "么", "哪", "为什么", "多少", "请", "stop", "not",
            ))
            if score >= threshold and not novel_intent:
                return True
        return False

    # Clause boundaries keep genuine appended commands: e.g. echo "挺帅的嘛，"
    # followed by the user saying "你给我打个招呼吧" must retain the command.
    clauses = list(re.finditer(r"[^,，。.!！？?;；\n]+[,，。.!！？?;；\n]*", user_text))
    left, right = 0, len(clauses)
    while left < right and matches(clauses[left].group()):
        left += 1
    while right > left and matches(clauses[right - 1].group()):
        right -= 1
    if clauses and (left or right < len(clauses)):
        remaining = user_text[clauses[left].start():clauses[right - 1].end()].strip() if left < right else ""
        short = normalise_echo_text(remaining)
        if (0 < len(short) < 4 and not re.search(r"停|等|不|别|stop|no", short, re.I)
                and any(short in reference for _, reference in candidates) and matches(user_text)):
            remaining = ""
        return remaining, {**detail, "reason": "echo_trimmed" if remaining else "echo_only"}
    if matches(user_text):
        return "", {**detail, "reason": "echo_only"}
    return user_text, {**detail, "reason": "kept"}


def likely_recent_tts_echo(server: Any, user_text: str) -> tuple[bool, dict[str, Any]]:
    filtered, detail = filter_recent_tts_echo(server, user_text)
    return bool(user_text.strip()) and not filtered, detail
