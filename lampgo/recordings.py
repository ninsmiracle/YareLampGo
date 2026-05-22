"""Recording catalog helpers.

Recorded motions are data, not code: a ``.csv`` file means the action exists,
and an optional sibling ``.txt`` file describes when the LLM should use it.
User recordings live in ``user/`` and shadow built-in recordings of the same
name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_RECORDING_DESCRIPTION_CHARS = 500
LED_EXPRESSION_KEYS = (
    "off",
    "red",
    "green",
    "blue",
    "white",
    "theater",
    "theaterred",
    "theatergreen",
    "theaterblue",
    "rainbow",
    "rainbowchase",
    "left",
    "right",
    "up",
    "down",
    "check",
    "cross",
    "exclaim",
    "question",
    "star",
    "music",
    "smiley",
    "sad",
    "heart",
    "surprised",
    "blush",
    "angry",
    "thinking",
    "sleep",
    "helpless",
    "cool",
    "focused",
    "wink",
)
RECORDING_EXPRESSION_HINTS: dict[str, str] = {
    "Stretch": "smiley",
    "bowing_head": "smiley",
    "dance1": "music",
    "dance2": "music",
    "deep_thinking": "focused",
    "excited": "smiley",
    "headshake1": "cross",
    "lie_flat": "sleep",
    "look_ahead": "focused",
    "look_around": "question",
    "nod": "check",
    "peep": "question",
    "raise_head": "surprised",
    "shy": "blush",
    "sneeze": "exclaim",
    "stand": "focused",
    "suqat_down": "helpless",
    "thinking": "thinking",
    "turn_back": "right",
    "upset": "sad",
    "wake_up": "surprised",
    "wave": "smiley",
}


def normalize_recording_description(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    text = " ".join(lines)
    return text[:MAX_RECORDING_DESCRIPTION_CHARS].strip()


def recording_description_path(csv_path: Path) -> Path:
    return csv_path.with_suffix(".txt")


def read_recording_description(csv_path: Path) -> str:
    path = recording_description_path(csv_path)
    if not path.exists():
        return ""
    try:
        return normalize_recording_description(path.read_text(encoding="utf-8"))
    except Exception:
        return ""


def write_recording_description(csv_path: Path, description: str) -> None:
    text = normalize_recording_description(description)
    path = recording_description_path(csv_path)
    if text:
        path.write_text(text + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def list_recording_catalog(recordings_dir: Path) -> list[dict[str, str]]:
    if not recordings_dir.exists():
        return []
    entries: dict[str, dict[str, str]] = {}
    for csv_path in recordings_dir.glob("*.csv"):
        entries[csv_path.stem] = {
            "name": csv_path.stem,
            "source": "builtin",
            "path": str(csv_path),
            "description": read_recording_description(csv_path),
        }
    user_dir = recordings_dir / "user"
    if user_dir.is_dir():
        for csv_path in user_dir.glob("*.csv"):
            entries[csv_path.stem] = {
                "name": csv_path.stem,
                "source": "user",
                "path": str(csv_path),
                "description": read_recording_description(csv_path),
            }
    return [entries[name] for name in sorted(entries)]


def build_recording_actions_prompt(recordings_dir: Path) -> str:
    catalog = list_recording_catalog(recordings_dir)
    lines = [
        "LED expression keys:",
        f"- Use these exact mode names in tool calls: {', '.join(LED_EXPRESSION_KEYS)}.",
        "Recorded action library (dynamic; loaded from CSV/TXT files):",
        "- Use `play_recording` with the exact `name` when the user's request, camera scene, emotion, or conversation context matches an action description.",
        "- If a listed recording includes an `expression=...` hint, prefer that expression before or alongside the action unless the user explicitly asked for a different mood.",
        "- Do not invent recording names. If no listed action fits, use another tool or speak instead.",
    ]
    if not catalog:
        lines.append("- No recordings are currently available.")
        return "\n".join(lines) + "\n\n"
    for item in catalog:
        name = item["name"]
        source = "我的录制" if item.get("source") == "user" else "内置动作"
        desc = item.get("description") or "暂无动作说明；仅在用户明确点名该动作名时使用。"
        expression = RECORDING_EXPRESSION_HINTS.get(name)
        if expression:
            lines.append(f"- name={name} | source={source} | expression={expression} | prompt={desc}")
        else:
            lines.append(f"- name={name} | source={source} | prompt={desc}")
    return "\n".join(lines) + "\n\n"
