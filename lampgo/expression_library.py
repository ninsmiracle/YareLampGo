"""Reusable eye, LED effect, and expression preset library.

The LCD eye payload remains owned by :mod:`lampgo.expression_clips`.  This
module adds the product-level many-to-many model without duplicating binary
assets: presets only reference an eye clip and an LED effect.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lampgo import personastore
from lampgo.core.led import led_expression_catalog
from lampgo.expression_clips import list_expression_clips, load_expression_clip

MAX_EYES_CURRENT = 5
MAX_EYE_BYTES = 256 * 1024
C6_INSTALLED_BUDGET_BYTES = 896 * 1024
C6_RESERVED_BYTES = 256 * 1024
C6_STAGING_BYTES = 256 * 1024

MAX_CUSTOM_LED_EFFECTS = 24
MAX_LED_EFFECT_BYTES = 8 * 1024
LED_EFFECT_BUDGET_BYTES = 192 * 1024
MAX_PRESETS = 64
MAX_PRESET_BYTES = 1024
PRESET_BUDGET_BYTES = 64 * 1024

ALLOWED_ROLES = {"mouth", "symbol", "direction", "accent"}
ALLOWED_TEMPLATES = {"mouth", "arrow", "heart", "pulse", "codex"}
ALLOWED_PLAYBACK = {"once", "loop"}
ALLOWED_DIRECTIONS = {"left", "right", "up", "down"}
ALLOWED_MOUTH_VARIANTS = {"smile", "open", "flat", "dizzy"}
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ExpressionLibraryError(ValueError):
    """Raised when an expression-library object is invalid or unsafe."""


def expression_library_root() -> Path:
    root = personastore.lampgo_home() / "expression_library"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _led_effect_dir() -> Path:
    path = expression_library_root() / "led_effects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _preset_dir() -> Path:
    path = expression_library_root() / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_library_id(value: str, *, field: str = "id") -> str:
    normalized = str(value or "").strip().lower()
    if not _SAFE_ID_RE.fullmatch(normalized):
        raise ExpressionLibraryError(f"{field} must be 1-32 lowercase letters, numbers, dash, or underscore")
    return normalized


def _read_json_objects(directory: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _encoded_json(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write_json(path: Path, data: dict[str, Any]) -> int:
    encoded = _encoded_json(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)
    return len(encoded)


def eye_public_id(clip_id: str) -> str:
    """Expose the legacy dizzy cache as the product-level ``dizzy_eyes``."""
    return "dizzy_eyes" if clip_id == "dizzy" else clip_id


def eye_storage_id(eye_clip_id: str) -> str:
    eye_clip_id = sanitize_library_id(eye_clip_id, field="eye_clip_id")
    return "dizzy" if eye_clip_id == "dizzy_eyes" else eye_clip_id


def list_eyes() -> list[dict[str, Any]]:
    eyes: list[dict[str, Any]] = []
    for manifest in list_expression_clips():
        clip_id = str(manifest.get("clip_id") or "").strip().lower()
        if not clip_id:
            continue
        lcd = dict(manifest.get("lcd") or {})
        source = dict(manifest.get("source") or {})
        source.setdefault("filename", manifest.get("source_filename"))
        source.setdefault("content_type", manifest.get("source_content_type"))
        eyes.append(
            {
                "eye_clip_id": eye_public_id(clip_id),
                "storage_clip_id": clip_id,
                "label": str(manifest.get("label") or manifest.get("expression") or clip_id),
                "fps": int(manifest.get("fps") or 0),
                "duration_ms": int(manifest.get("duration_ms") or 0),
                "frame_count": int(manifest.get("frame_count") or 0),
                "lcd": lcd,
                "source": source,
                "default_led_effect_id": manifest.get("default_led_effect_id"),
                "sync": dict(manifest.get("sync") or {}),
            }
        )
    return eyes


def load_eye(eye_clip_id: str) -> dict[str, Any]:
    storage_id = eye_storage_id(eye_clip_id)
    load_expression_clip(storage_id)
    for eye in list_eyes():
        if eye["storage_clip_id"] == storage_id:
            return eye
    raise ExpressionLibraryError(f"eye clip not found: {eye_clip_id}")


def eye_source_path(eye_clip_id: str) -> Path:
    eye = load_eye(eye_clip_id)
    storage_id = str(eye["storage_clip_id"])
    manifest = load_expression_clip(storage_id)
    directory = personastore.lampgo_home() / "expression_clips" / storage_id
    filename = str((manifest.get("source") or {}).get("stored_filename") or "")
    if filename:
        candidate = directory / Path(filename).name
        if candidate.exists():
            return candidate
    candidates = sorted(path for path in directory.glob("source.*") if path.is_file())
    if not candidates:
        raise ExpressionLibraryError(f"eye source not found: {eye_clip_id}")
    return candidates[0]


def set_eye_default_led(eye_clip_id: str, led_effect_id: str | None) -> dict[str, Any]:
    storage_id = eye_storage_id(eye_clip_id)
    manifest = load_expression_clip(storage_id)
    normalized_effect = str(led_effect_id or "").strip().lower() or None
    if normalized_effect:
        load_led_effect(normalized_effect)
    manifest["default_led_effect_id"] = normalized_effect
    path = personastore.lampgo_home() / "expression_clips" / storage_id / "manifest.json"
    _atomic_write_json(path, manifest)
    return load_eye(eye_clip_id)


def _builtin_role(name: str) -> str:
    if name in {"left", "right", "up", "down", "check", "cross", "exclaim", "question"}:
        return "direction" if name in ALLOWED_DIRECTIONS else "symbol"
    if name in {"smiley", "sad", "surprised", "blush", "angry", "thinking", "sleep", "helpless", "cool"}:
        return "mouth"
    return "accent"


def _builtin_parameters(name: str) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "brightness": {"type": "integer", "minimum": 1, "maximum": 96, "default": 64},
        "intensity": {"type": "number", "minimum": 0.1, "maximum": 1.0, "default": 1.0},
    }
    if name in ALLOWED_DIRECTIONS:
        schema["direction"] = {"type": "string", "enum": sorted(ALLOWED_DIRECTIONS), "default": name}
    return schema


def _virtual_effects() -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    for item in led_expression_catalog():
        name = str(item["name"])
        effects.append(
            {
                "effect_id": name,
                "label": str(item.get("label") or name),
                "role": _builtin_role(name),
                "source": "builtin",
                "animated": bool(item.get("animated")),
                "mode": int(item["mode"]),
                "parameter_schema": _builtin_parameters(name),
            }
        )
    effects.append(
        {
            "effect_id": "arrow",
            "label": "参数化箭头",
            "role": "direction",
            "source": "template",
            "animated": False,
            "program": {"version": 1, "template": "arrow", "defaults": {"direction": "right", "color": "#00ff88"}},
            "parameter_schema": {
                "direction": {"type": "string", "enum": sorted(ALLOWED_DIRECTIONS), "default": "right"},
                "color": {"type": "string", "format": "color", "default": "#00ff88"},
                "brightness": {"type": "integer", "minimum": 1, "maximum": 96, "default": 64},
                "intensity": {"type": "number", "minimum": 0.1, "maximum": 1.0, "default": 1.0},
            },
        }
    )
    effects.append(
        {
            "effect_id": "codex",
            "label": "CODEX 闪烁字标",
            "role": "symbol",
            "source": "template",
            "animated": True,
            "program": {
                "version": 1,
                "template": "codex",
                "defaults": {
                    "color": "#f4f4f4",
                    "secondary_color": "#00d8ff",
                    "brightness": 64,
                    "intensity": 1.0,
                },
            },
            "parameter_schema": _template_parameter_schema("codex"),
        }
    )
    if any(eye["storage_clip_id"] == "dizzy" for eye in list_eyes()):
        effects.append(
            {
                "effect_id": "dizzy_mouth",
                "label": "眩晕大嘴",
                "role": "mouth",
                "source": "migration",
                "animated": True,
                "program": {
                    "version": 1,
                    "template": "mouth",
                    "variant": "dizzy",
                    "defaults": {"color": "#ffffff", "secondary_color": "#ff2d7a", "intensity": 1.0},
                },
                "parameter_schema": _template_parameter_schema("mouth"),
            }
        )
    return effects


def _template_parameter_schema(template: str) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "color": {"type": "string", "format": "color", "default": "#ffffff"},
        "brightness": {"type": "integer", "minimum": 1, "maximum": 96, "default": 64},
        "intensity": {"type": "number", "minimum": 0.1, "maximum": 1.0, "default": 1.0},
    }
    if template == "arrow":
        schema["direction"] = {"type": "string", "enum": sorted(ALLOWED_DIRECTIONS), "default": "right"}
    return schema


def _normalize_color(value: Any, *, field: str) -> str:
    color = str(value or "").strip().lower()
    if not _HEX_COLOR_RE.fullmatch(color):
        raise ExpressionLibraryError(f"{field} must be a #RRGGBB color")
    return color


def _validate_program(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ExpressionLibraryError("program must be an object")
    if int(raw.get("version") or 1) != 1:
        raise ExpressionLibraryError("program.version must be 1")
    template = str(raw.get("template") or "").strip().lower()
    if template not in ALLOWED_TEMPLATES:
        raise ExpressionLibraryError(f"program.template must be one of: {', '.join(sorted(ALLOWED_TEMPLATES))}")
    variant = str(raw.get("variant") or "").strip().lower()
    if template == "mouth" and not variant:
        variant = "open"
    if template == "mouth" and variant and variant not in ALLOWED_MOUTH_VARIANTS:
        raise ExpressionLibraryError(f"mouth variant must be one of: {', '.join(sorted(ALLOWED_MOUTH_VARIANTS))}")
    defaults_raw = raw.get("defaults") or {}
    if not isinstance(defaults_raw, dict):
        raise ExpressionLibraryError("program.defaults must be an object")
    defaults: dict[str, Any] = {}
    for key in ("color", "secondary_color"):
        if key in defaults_raw:
            defaults[key] = _normalize_color(defaults_raw[key], field=f"program.defaults.{key}")
    if "brightness" in defaults_raw:
        defaults["brightness"] = max(1, min(96, int(defaults_raw["brightness"])))
    if "intensity" in defaults_raw:
        defaults["intensity"] = max(0.1, min(1.0, float(defaults_raw["intensity"])))
    if "direction" in defaults_raw:
        direction = str(defaults_raw["direction"]).strip().lower()
        if direction not in ALLOWED_DIRECTIONS:
            raise ExpressionLibraryError("program.defaults.direction is invalid")
        defaults["direction"] = direction
    program: dict[str, Any] = {"version": 1, "template": template, "defaults": defaults}
    if variant:
        program["variant"] = variant
    return program


def list_led_effects() -> list[dict[str, Any]]:
    custom = _read_json_objects(_led_effect_dir())
    by_id = {str(item["effect_id"]): item for item in _virtual_effects()}
    for item in custom:
        effect_id = str(item.get("effect_id") or "")
        if effect_id:
            by_id[effect_id] = item
    return list(by_id.values())


def load_led_effect(effect_id: str) -> dict[str, Any]:
    effect_id = sanitize_library_id(effect_id, field="led_effect_id")
    for effect in list_led_effects():
        if effect["effect_id"] == effect_id:
            return effect
    raise ExpressionLibraryError(f"LED effect not found: {effect_id}")


def save_led_effect(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ExpressionLibraryError("LED effect must be an object")
    effect_id = sanitize_library_id(str(raw.get("effect_id") or ""), field="effect_id")
    if any(effect["effect_id"] == effect_id for effect in _virtual_effects()):
        raise ExpressionLibraryError("built-in and migration LED effects cannot be overwritten")
    role = str(raw.get("role") or "accent").strip().lower()
    if role not in ALLOWED_ROLES:
        raise ExpressionLibraryError(f"role must be one of: {', '.join(sorted(ALLOWED_ROLES))}")
    label = str(raw.get("label") or effect_id).strip()[:64]
    program = _validate_program(raw.get("program"))
    item = {
        "effect_id": effect_id,
        "label": label,
        "role": role,
        "source": "custom",
        "animated": program["template"] in {"mouth", "heart", "pulse", "codex"},
        "program": program,
        "parameter_schema": _template_parameter_schema(program["template"]),
    }
    encoded = _encoded_json(item)
    if len(encoded) > MAX_LED_EFFECT_BYTES:
        raise ExpressionLibraryError(f"LED effect exceeds {MAX_LED_EFFECT_BYTES} byte limit")
    directory = _led_effect_dir()
    path = directory / f"{effect_id}.json"
    existing = _read_json_objects(directory)
    if not path.exists() and len(existing) >= MAX_CUSTOM_LED_EFFECTS:
        raise ExpressionLibraryError(f"maximum custom LED effects reached: {MAX_CUSTOM_LED_EFFECTS}")
    used_without_current = sum(
        len(_encoded_json(item)) for item in existing if str(item.get("effect_id") or "") != effect_id
    )
    if used_without_current + len(encoded) > LED_EFFECT_BUDGET_BYTES:
        raise ExpressionLibraryError("custom LED effect storage budget exceeded")
    _atomic_write_json(path, item)
    return item


def _normalize_led_params(raw: Any, effect: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ExpressionLibraryError("led_params must be an object")
    params: dict[str, Any] = {}
    if "color" in raw:
        params["color"] = _normalize_color(raw["color"], field="led_params.color")
    if "secondary_color" in raw:
        params["secondary_color"] = _normalize_color(raw["secondary_color"], field="led_params.secondary_color")
    if "brightness" in raw:
        brightness = int(raw["brightness"])
        if not 1 <= brightness <= 96:
            raise ExpressionLibraryError("led_params.brightness must be 1-96")
        params["brightness"] = brightness
    if "intensity" in raw:
        intensity = float(raw["intensity"])
        if not 0.1 <= intensity <= 1.0:
            raise ExpressionLibraryError("led_params.intensity must be 0.1-1.0")
        params["intensity"] = intensity
    if "direction" in raw:
        direction = str(raw["direction"]).strip().lower()
        if direction not in ALLOWED_DIRECTIONS:
            raise ExpressionLibraryError("led_params.direction is invalid")
        params["direction"] = direction
    allowed = {"color", "secondary_color", "brightness", "intensity", "direction"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ExpressionLibraryError(f"unsupported led_params: {', '.join(unknown)}")
    if effect and effect.get("source") == "builtin" and "color" in params:
        # Firmware built-ins use their authored palettes. Keep the value for UI
        # round-tripping, while the device may ignore it.
        params["color"] = params["color"]
    return params


def _stored_presets() -> list[dict[str, Any]]:
    return _read_json_objects(_preset_dir())


def _virtual_dizzy_preset() -> dict[str, Any] | None:
    if not any(eye["eye_clip_id"] == "dizzy_eyes" for eye in list_eyes()):
        return None
    return {
        "preset_id": "dizzy",
        "label": "眩晕",
        "description": "蚊香眼与眩晕大嘴联动",
        "eye_clip_id": "dizzy_eyes",
        "led_effect_id": "dizzy_mouth",
        "led_params": {},
        "playback": "once",
        "duration_ms": 3000,
        "source": "migration",
    }


def list_expression_presets() -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    dizzy = _virtual_dizzy_preset()
    if dizzy:
        by_id["dizzy"] = dizzy
    for item in _stored_presets():
        preset_id = str(item.get("preset_id") or "")
        if preset_id:
            by_id[preset_id] = item
    return list(by_id.values())


def load_expression_preset(preset_id: str) -> dict[str, Any]:
    preset_id = sanitize_library_id(preset_id, field="preset_id")
    for preset in list_expression_presets():
        if preset["preset_id"] == preset_id:
            return preset
    raise ExpressionLibraryError(f"expression preset not found: {preset_id}")


def save_expression_preset(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ExpressionLibraryError("preset must be an object")
    preset_id = sanitize_library_id(str(raw.get("preset_id") or ""), field="preset_id")
    eye_id = str(raw.get("eye_clip_id") or "").strip().lower() or None
    led_id = str(raw.get("led_effect_id") or "").strip().lower() or None
    if not eye_id and not led_id:
        raise ExpressionLibraryError("preset requires an eye_clip_id or led_effect_id")
    eye = load_eye(eye_id) if eye_id else None
    effect = load_led_effect(led_id) if led_id else None
    playback = str(raw.get("playback") or "once").strip().lower()
    if playback not in ALLOWED_PLAYBACK:
        raise ExpressionLibraryError("playback must be once or loop")
    duration_ms = int(raw.get("duration_ms") or (eye or {}).get("duration_ms") or 3000)
    if not 2500 <= duration_ms <= 3500:
        raise ExpressionLibraryError("duration_ms must be 2500-3500")
    item = {
        "preset_id": preset_id,
        "label": str(raw.get("label") or preset_id).strip()[:64],
        "description": str(raw.get("description") or "").strip()[:240],
        "eye_clip_id": eye_id,
        "led_effect_id": led_id,
        "led_params": _normalize_led_params(raw.get("led_params"), effect),
        "playback": playback,
        "duration_ms": duration_ms,
        "source": str(raw.get("source") or "user").strip()[:32],
    }
    encoded = _encoded_json(item)
    if len(encoded) > MAX_PRESET_BYTES:
        raise ExpressionLibraryError(f"preset exceeds {MAX_PRESET_BYTES} byte limit")
    directory = _preset_dir()
    path = directory / f"{preset_id}.json"
    existing = _stored_presets()
    if not path.exists() and len(existing) >= MAX_PRESETS:
        raise ExpressionLibraryError(f"maximum expression presets reached: {MAX_PRESETS}")
    used_without_current = sum(
        len(_encoded_json(item)) for item in existing if str(item.get("preset_id") or "") != preset_id
    )
    if used_without_current + len(encoded) > PRESET_BUDGET_BYTES:
        raise ExpressionLibraryError("expression preset storage budget exceeded")
    _atomic_write_json(path, item)
    return item


def resolve_expression(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ExpressionLibraryError("expression request must be an object")
    preset: dict[str, Any] | None = None
    preset_id = str(raw.get("preset_id") or "").strip().lower()
    expression_id = str(raw.get("expression_id") or "").strip().lower()
    if preset_id:
        preset = load_expression_preset(preset_id)
    elif expression_id:
        try:
            preset = load_expression_preset(expression_id)
        except ExpressionLibraryError:
            preset = None

    eye_id = str(raw.get("eye_clip_id") or (preset or {}).get("eye_clip_id") or "").strip().lower() or None
    led_explicit = "led_effect_id" in raw
    led_id = str(raw.get("led_effect_id") or "").strip().lower() if led_explicit else str(
        (preset or {}).get("led_effect_id") or ""
    ).strip().lower()
    if expression_id and not preset and not eye_id and not led_id:
        try:
            load_eye(expression_id)
            eye_id = expression_id
        except Exception:
            load_led_effect(expression_id)
            led_id = expression_id

    eye = load_eye(eye_id) if eye_id else None
    if not led_explicit and not led_id and eye:
        led_id = str(eye.get("default_led_effect_id") or "").strip().lower()
    effect = load_led_effect(led_id) if led_id else None
    if not eye and not effect:
        raise ExpressionLibraryError("expression requires an eye clip or LED effect")

    merged_params = dict((preset or {}).get("led_params") or {})
    merged_params.update(raw.get("led_params") or {})
    playback = str(raw.get("playback") or (preset or {}).get("playback") or "once").strip().lower()
    if playback not in ALLOWED_PLAYBACK:
        raise ExpressionLibraryError("playback must be once or loop")
    duration_ms = int(
        raw.get("duration_ms") or (preset or {}).get("duration_ms") or (eye or {}).get("duration_ms") or 3000
    )
    if not 2500 <= duration_ms <= 3500:
        raise ExpressionLibraryError("duration_ms must be 2500-3500")
    return {
        "preset_id": (preset or {}).get("preset_id"),
        "eye_clip_id": eye_id,
        "eye_storage_clip_id": (eye or {}).get("storage_clip_id"),
        "led_effect_id": led_id or None,
        "led_effect": effect,
        "led_params": _normalize_led_params(merged_params, effect),
        "playback": playback,
        "duration_ms": duration_ms,
        "persist": False,
    }


def expression_capabilities() -> dict[str, Any]:
    eyes = list_eyes()
    custom_effects = [item for item in _read_json_objects(_led_effect_dir())]
    presets = _stored_presets()
    eye_used = sum(int((item.get("lcd") or {}).get("bytes") or 0) for item in eyes)
    led_used = sum(len(_encoded_json(item)) for item in custom_effects)
    preset_used = sum(len(_encoded_json(item)) for item in presets)
    return {
        "eyes": {
            "installed": len(eyes),
            "max_count": MAX_EYES_CURRENT,
            "used_bytes": eye_used,
            "budget_bytes": C6_INSTALLED_BUDGET_BYTES,
            "single_max_bytes": MAX_EYE_BYTES,
            "reserved_bytes": C6_RESERVED_BYTES,
            "staging_bytes": C6_STAGING_BYTES,
        },
        "led_effects": {
            "installed_custom": len(custom_effects),
            "builtin": len(led_expression_catalog()),
            "max_custom_count": MAX_CUSTOM_LED_EFFECTS,
            "used_bytes": led_used,
            "budget_bytes": LED_EFFECT_BUDGET_BYTES,
            "single_max_bytes": MAX_LED_EFFECT_BYTES,
        },
        "presets": {
            "installed": len(presets),
            "max_count": MAX_PRESETS,
            "used_bytes": preset_used,
            "budget_bytes": PRESET_BUDGET_BYTES,
            "single_max_bytes": MAX_PRESET_BYTES,
        },
    }


def build_expression_prompt() -> str:
    eyes = ", ".join(item["eye_clip_id"] for item in list_eyes()) or "none"
    effects = ", ".join(item["effect_id"] for item in list_led_effects())
    presets = ", ".join(item["preset_id"] for item in list_expression_presets()) or "none"
    return (
        "Expression capabilities:\n"
        f"- Eye clips (C6): {eyes}\n"
        f"- LED effects (S3): {effects}\n"
        f"- Saved presets: {presets}\n"
        "Use saved presets when possible. A transient composition may be played without saving; "
        "saving a new preset requires explicit user confirmation."
    )


def expression_schemas() -> dict[str, Any]:
    return {
        "eye_clip": {
            "type": "object",
            "required": ["eye_clip_id", "fps", "duration_ms", "frame_count", "lcd"],
            "properties": {
                "eye_clip_id": {"type": "string", "pattern": _SAFE_ID_RE.pattern},
                "default_led_effect_id": {"type": ["string", "null"]},
                "fps": {"type": "integer", "minimum": 8, "maximum": 12},
                "duration_ms": {"type": "integer", "minimum": 2500, "maximum": 3500},
            },
        },
        "led_effect": {
            "type": "object",
            "required": ["effect_id", "role", "program"],
            "properties": {
                "effect_id": {"type": "string", "pattern": _SAFE_ID_RE.pattern},
                "role": {"enum": sorted(ALLOWED_ROLES)},
                "program": {
                    "type": "object",
                    "properties": {"version": {"const": 1}, "template": {"enum": sorted(ALLOWED_TEMPLATES)}},
                },
            },
        },
        "expression_preset": {
            "type": "object",
            "required": ["preset_id", "playback", "duration_ms"],
            "properties": {
                "preset_id": {"type": "string", "pattern": _SAFE_ID_RE.pattern},
                "eye_clip_id": {"type": ["string", "null"]},
                "led_effect_id": {"type": ["string", "null"]},
                "playback": {"enum": sorted(ALLOWED_PLAYBACK)},
                "duration_ms": {"type": "integer", "minimum": 2500, "maximum": 3500},
            },
        },
    }
