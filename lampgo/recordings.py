"""Recording catalog helpers.

Recorded motions are data, not code: a ``.csv`` file means the action exists,
and an optional sibling ``.txt`` file describes when the LLM should use it.
User recordings live in ``user/`` and shadow built-in recordings of the same
name.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

MAX_RECORDING_DESCRIPTION_CHARS = 500
MAX_RECORDING_NAME_CHARS = 64
RECORDING_NAME_ERROR = "动作名称仅支持中文、字母、数字、下划线和短横线（最多 64 个字符）"
_SAFE_PRESET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
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
    "myu7gt",
)
RECORDING_EXPRESSION_HINTS: dict[str, str] = {
    "Stretch": "star",
    "bowing_head": "down",
    "dance1": "music",
    "dance2": "rainbowchase",
    "deep_thinking": "focused",
    "excited": "heart",
    "headshake1": "cross",
    "lie_flat": "sleep",
    "look_ahead": "cool",
    "look_around": "question",
    "nod": "check",
    "peep": "wink",
    "raise_head": "up",
    "shy": "blush",
    "sneeze": "exclaim",
    "stand": "white",
    "suqat_down": "helpless",
    "thinking": "thinking",
    "turn_back": "right",
    "upset": "sad",
    "wake_up": "surprised",
    "wave": "smiley",
}


def normalize_recording_name(value: Any) -> str:
    """Return a filesystem-safe Unicode recording name, or an empty string.

    User recordings are UTF-8 CSV/TXT files on local storage, so Chinese names
    are safe. Keep the allowed character set deliberately narrow to prevent
    path traversal and keep the same rule across Web, CLI, and playback.
    """
    name = unicodedata.normalize("NFC", str(value or "").strip())
    if not name or len(name) > MAX_RECORDING_NAME_CHARS:
        return ""
    if not all(char.isalnum() or char in "_-" for char in name):
        return ""
    return name


def _normalize_expression(value: Any) -> str:
    key = "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())
    return key if key in LED_EXPRESSION_KEYS else ""


def _normalize_expression_preset(value: Any) -> str:
    preset_id = str(value or "").strip().lower()
    return preset_id if _SAFE_PRESET_ID_RE.match(preset_id) else ""


def normalize_recording_description(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    text = " ".join(lines)
    return text[:MAX_RECORDING_DESCRIPTION_CHARS].strip()


def recording_description_path(csv_path: Path) -> Path:
    return csv_path.with_suffix(".txt")


def parse_recording_metadata(text: str) -> dict[str, str]:
    """Parse optional recording sidecar metadata, preserving old plain text files."""
    description_parts: list[str] = []
    expression = ""
    expression_preset = ""

    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        normalized_key = key.strip().lower()
        if sep and normalized_key == "expression":
            expression = _normalize_expression(value) or expression
        elif sep and normalized_key in {"expression_preset", "preset", "preset_id"}:
            expression_preset = _normalize_expression_preset(value) or expression_preset
        elif sep and normalized_key in {"prompt", "description"}:
            description_parts.append(value.strip())
        else:
            description_parts.append(line)

    return {
        "description": normalize_recording_description("\n".join(description_parts)),
        "expression": expression,
        "expression_preset": expression_preset,
    }


def read_recording_metadata(csv_path: Path) -> dict[str, str]:
    path = recording_description_path(csv_path)
    if not path.exists():
        return {"description": "", "expression": "", "expression_preset": ""}
    try:
        return parse_recording_metadata(path.read_text(encoding="utf-8"))
    except Exception:
        return {"description": "", "expression": "", "expression_preset": ""}


def read_recording_description(csv_path: Path) -> str:
    return read_recording_metadata(csv_path)["description"]


def write_recording_description(
    csv_path: Path,
    description: str,
    expression: str = "",
    expression_preset: str = "",
) -> None:
    text = normalize_recording_description(description)
    expression = _normalize_expression(expression)
    expression_preset = _normalize_expression_preset(expression_preset)
    path = recording_description_path(csv_path)
    lines = []
    if expression_preset:
        lines.append(f"expression_preset={expression_preset}")
    if expression:
        lines.append(f"expression={expression}")
    if text:
        lines.append(f"prompt={text}")
    if lines:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def _stable_name_index(name: str) -> int:
    return sum((idx + 1) * ord(ch) for idx, ch in enumerate(name))


def _assign_catalog_expressions(entries: dict[str, dict[str, str]]) -> None:
    """Assign a stable, preferably unique LED expression to every recording."""
    if not entries:
        return

    expression_keys = [key for key in LED_EXPRESSION_KEYS if key != "off"]
    used: set[str] = set()
    for name in sorted(entries):
        fixed = _normalize_expression(entries[name].get("expression", ""))
        if fixed:
            entries[name]["expression"] = fixed
            used.add(fixed)
            continue
        preferred = RECORDING_EXPRESSION_HINTS.get(name)
        expression = preferred if preferred in LED_EXPRESSION_KEYS and preferred not in used else ""
        if not expression:
            start = _stable_name_index(name) % len(expression_keys)
            for offset in range(len(expression_keys)):
                candidate = expression_keys[(start + offset) % len(expression_keys)]
                if candidate not in used:
                    expression = candidate
                    break
            if not expression:
                expression = expression_keys[start]
        entries[name]["expression"] = expression
        used.add(expression)


def list_recording_catalog(recordings_dir: Path) -> list[dict[str, str]]:
    if not recordings_dir.exists():
        return []
    entries: dict[str, dict[str, str]] = {}
    for csv_path in recordings_dir.glob("*.csv"):
        metadata = read_recording_metadata(csv_path)
        entries[csv_path.stem] = {
            "name": csv_path.stem,
            "source": "builtin",
            "path": str(csv_path),
            "description": metadata["description"],
            "expression": metadata["expression"],
            "expression_preset": metadata["expression_preset"],
        }
    user_dir = recordings_dir / "user"
    if user_dir.is_dir():
        for csv_path in user_dir.glob("*.csv"):
            metadata = read_recording_metadata(csv_path)
            entries[csv_path.stem] = {
                "name": csv_path.stem,
                "source": "user",
                "path": str(csv_path),
                "description": metadata["description"],
                "expression": metadata["expression"],
                "expression_preset": metadata["expression_preset"],
            }
    _assign_catalog_expressions(entries)
    return [entries[name] for name in sorted(entries)]


def build_recording_actions_prompt(recordings_dir: Path) -> str:
    catalog = list_recording_catalog(recordings_dir)
    lines = [
        "LED expression keys:",
        f"- Use these exact mode names in tool calls: {', '.join(LED_EXPRESSION_KEYS)}.",
        "Recorded action library (dynamic; loaded from CSV/TXT files):",
        "- Use `play_recording` with the exact `name` when the user's request, camera scene, emotion, "
        "or conversation context matches an action description.",
        "- If a listed recording includes `expression_preset=...`, pass it to `play_recording` so C6 eyes "
        "and the S3 LED panel start together. Otherwise use `expression=...` as the LED-only fallback.",
        "- Do not invent recording names. If no listed action fits, use another tool or speak instead.",
    ]
    if not catalog:
        lines.append("- No recordings are currently available.")
        return "\n".join(lines) + "\n\n"
    for item in catalog:
        name = item["name"]
        source = "我的录制" if item.get("source") == "user" else "内置动作"
        desc = item.get("description") or "暂无动作说明；仅在用户明确点名该动作名时使用。"
        expression = item.get("expression")
        expression_preset = item.get("expression_preset")
        if expression_preset:
            fallback = f" | expression={expression}" if expression else ""
            lines.append(
                f"- name={name} | source={source} | expression_preset={expression_preset}{fallback} | prompt={desc}"
            )
        elif expression:
            lines.append(f"- name={name} | source={source} | expression={expression} | prompt={desc}")
        else:
            lines.append(f"- name={name} | source={source} | prompt={desc}")
    return "\n".join(lines) + "\n\n"
