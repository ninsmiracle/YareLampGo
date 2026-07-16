"""Safe, deterministic LED pixel-clip assets for the S3 matrix.

The authoring format is JSON so browsers and LLMs can create it.  Devices only
receive the compact LEF1 binary compiled here; no user-authored code is ever
executed on the S3.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import zlib
from pathlib import Path
from typing import Any

from lampgo import personastore

LED_WIDTH = 51
LED_HEIGHT = 9
LED_PIXEL_COUNT = 447
LED_FPS = 10
LED_TICK_COUNT = 30
LED_FRAME_BYTES = (LED_PIXEL_COUNT + 1) // 2
LED_PALETTE_SIZE = 16
MAX_LED_EFFECT_BYTES = 8 * 1024
MAX_CUSTOM_LED_EFFECTS = 24
LED_EFFECT_BUDGET_BYTES = 192 * 1024

LEF_MAGIC = b"LEF1"
LEF_VERSION = 1
LEF_HEADER = struct.Struct("<4s8BHHII8B")
LEF_HEADER_BYTES = LEF_HEADER.size

_ROW_LENGTHS = (47, 49, 51, 51, 51, 51, 51, 49, 47)
_ROW_STARTS = (0, 47, 96, 147, 198, 249, 300, 351, 400)
_ROLE_NAMES = ("primary", "secondary", "accent")
_SAFE_SYMBOLS = frozenset(".123456789ABCDEF")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class LedEffectError(ValueError):
    """Raised when an LED authoring document or package is invalid."""


def _safe_effect_id(effect_id: str) -> str:
    normalized = str(effect_id or "").strip().lower()
    if not _SAFE_ID_RE.fullmatch(normalized):
        raise LedEffectError("effect_id must be 1-32 lowercase letters, numbers, dash, or underscore")
    return normalized


def led_effect_root() -> Path:
    path = personastore.lampgo_home() / "expression_library" / "led_effects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _encoded_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _normalize_hex_color(value: Any, *, field: str) -> str:
    color = str(value or "").strip().lower()
    if len(color) != 7 or color[0] != "#":
        raise LedEffectError(f"{field} must be a #RRGGBB color")
    try:
        int(color[1:], 16)
    except ValueError as exc:
        raise LedEffectError(f"{field} must be a #RRGGBB color") from exc
    return color


def _rgb_bytes(color: str) -> bytes:
    return bytes((int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)))


def _physical_index(row: int, col: int) -> int | None:
    """Map the editor's front-facing 51x9 grid to the wired pixel order."""
    wired_row = LED_HEIGHT - 1 - row
    wired_col = LED_WIDTH - 1 - col
    row_length = _ROW_LENGTHS[wired_row]
    left_pad = (LED_WIDTH - row_length) // 2
    local_col = wired_col - left_pad
    if local_col < 0 or local_col >= row_length:
        return None
    return _ROW_STARTS[wired_row] + local_col


def _pack_frame(rows: list[str], symbol_indexes: dict[str, int]) -> bytes:
    if len(rows) != LED_HEIGHT:
        raise LedEffectError(f"each LED frame must contain {LED_HEIGHT} rows")
    pixels = bytearray(LED_PIXEL_COUNT)
    for row_index, raw_row in enumerate(rows):
        row = str(raw_row)
        if len(row) != LED_WIDTH:
            raise LedEffectError(f"frame row {row_index} must contain exactly {LED_WIDTH} cells")
        for col_index, symbol in enumerate(row):
            if symbol not in symbol_indexes:
                raise LedEffectError(f"frame uses undefined palette symbol: {symbol}")
            physical = _physical_index(row_index, col_index)
            if physical is not None:
                pixels[physical] = symbol_indexes[symbol]
    packed = bytearray(LED_FRAME_BYTES)
    for index, palette_index in enumerate(pixels):
        if index & 1:
            packed[index // 2] |= palette_index & 0x0F
        else:
            packed[index // 2] = (palette_index & 0x0F) << 4
    return bytes(packed)


def compile_led_program(program: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """Validate a v2 pixel-clip program and compile it to LEF1."""
    if not isinstance(program, dict):
        raise LedEffectError("program must be an object")
    if int(program.get("version") or 0) != 2 or program.get("type") != "pixel_clip":
        raise LedEffectError("program must use version=2 and type=pixel_clip")
    if int(program.get("fps") or LED_FPS) != LED_FPS:
        raise LedEffectError(f"pixel clips must use {LED_FPS} fps")

    raw_palette = program.get("palette")
    if not isinstance(raw_palette, dict) or not raw_palette:
        raise LedEffectError("program.palette must be a non-empty object")
    palette: dict[str, str] = {".": "#000000"}
    for raw_symbol, raw_color in raw_palette.items():
        symbol = str(raw_symbol)
        if symbol not in _SAFE_SYMBOLS:
            raise LedEffectError("palette symbols must be one of .123456789ABCDEF")
        color = _normalize_hex_color(raw_color, field=f"program.palette.{symbol}")
        if symbol == "." and color != "#000000":
            raise LedEffectError("palette symbol . is reserved for #000000")
        palette[symbol] = color
    ordered_symbols = ["."] + sorted(symbol for symbol in palette if symbol != ".")
    if len(ordered_symbols) > LED_PALETTE_SIZE:
        raise LedEffectError(f"pixel clips support at most {LED_PALETTE_SIZE} colors")
    symbol_indexes = {symbol: index for index, symbol in enumerate(ordered_symbols)}

    raw_roles = program.get("roles") or {}
    if not isinstance(raw_roles, dict):
        raise LedEffectError("program.roles must be an object")
    roles: dict[str, str] = {}
    role_indexes = [255, 255, 255]
    for role_index, role in enumerate(_ROLE_NAMES):
        if role not in raw_roles:
            continue
        symbol = str(raw_roles[role])
        if symbol == "." or symbol not in symbol_indexes:
            raise LedEffectError(f"program.roles.{role} must reference a visible palette symbol")
        roles[role] = symbol
        role_indexes[role_index] = symbol_indexes[symbol]
    unknown_roles = sorted(set(raw_roles) - set(_ROLE_NAMES))
    if unknown_roles:
        raise LedEffectError(f"unsupported palette roles: {', '.join(unknown_roles)}")

    raw_frames = program.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise LedEffectError("program.frames must be a non-empty array")
    unique_frames: list[bytes] = []
    frame_indexes: dict[bytes, int] = {}
    timeline: list[int] = []
    normalized_frames: list[dict[str, Any]] = []
    for frame_number, raw_frame in enumerate(raw_frames):
        if not isinstance(raw_frame, dict):
            raise LedEffectError(f"program.frames[{frame_number}] must be an object")
        raw_rows = raw_frame.get("rows")
        if not isinstance(raw_rows, list):
            raise LedEffectError(f"program.frames[{frame_number}].rows must be an array")
        rows = [str(row) for row in raw_rows]
        ticks = int(raw_frame.get("ticks") or 1)
        if ticks < 1 or ticks > LED_TICK_COUNT:
            raise LedEffectError(f"program.frames[{frame_number}].ticks must be 1-{LED_TICK_COUNT}")
        packed = _pack_frame(rows, symbol_indexes)
        unique_index = frame_indexes.get(packed)
        if unique_index is None:
            unique_index = len(unique_frames)
            if unique_index >= LED_TICK_COUNT:
                raise LedEffectError(f"pixel clips support at most {LED_TICK_COUNT} unique frames")
            frame_indexes[packed] = unique_index
            unique_frames.append(packed)
        timeline.extend([unique_index] * ticks)
        normalized_frames.append({"rows": rows, "ticks": ticks})
    if len(timeline) != LED_TICK_COUNT:
        raise LedEffectError(f"pixel clip timeline must contain exactly {LED_TICK_COUNT} ticks")

    palette_bytes = b"".join(_rgb_bytes(palette[symbol]) for symbol in ordered_symbols)
    payload = palette_bytes + bytes(timeline) + b"".join(unique_frames)
    crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    header = LEF_HEADER.pack(
        LEF_MAGIC,
        LEF_VERSION,
        LED_WIDTH,
        LED_HEIGHT,
        LED_FPS,
        LED_TICK_COUNT,
        len(unique_frames),
        len(ordered_symbols),
        0,
        LED_FRAME_BYTES,
        LEF_HEADER_BYTES,
        len(payload),
        crc32,
        *role_indexes,
        0,
        0,
        0,
        0,
        0,
    )
    package = header + payload
    if len(package) > MAX_LED_EFFECT_BYTES:
        raise LedEffectError(f"compiled LED effect exceeds {MAX_LED_EFFECT_BYTES} byte limit")
    normalized = {
        "version": 2,
        "type": "pixel_clip",
        "fps": LED_FPS,
        "palette": {symbol: palette[symbol] for symbol in ordered_symbols},
        "roles": roles,
        "frames": normalized_frames,
    }
    return normalized, package


def inspect_led_package(package: bytes) -> dict[str, Any]:
    """Parse and checksum a LEF1 package, primarily for tests and diagnostics."""
    if len(package) < LEF_HEADER_BYTES:
        raise LedEffectError("LEF1 package is truncated")
    unpacked = LEF_HEADER.unpack_from(package)
    magic = unpacked[0]
    version, width, height, fps, ticks, frames, colors, flags = unpacked[1:9]
    frame_bytes, header_bytes, payload_bytes, expected_crc = unpacked[9:13]
    primary, secondary, accent = unpacked[13:16]
    if magic != LEF_MAGIC or version != LEF_VERSION:
        raise LedEffectError("unsupported LED package")
    if (width, height, fps, ticks, frame_bytes, header_bytes) != (
        LED_WIDTH,
        LED_HEIGHT,
        LED_FPS,
        LED_TICK_COUNT,
        LED_FRAME_BYTES,
        LEF_HEADER_BYTES,
    ):
        raise LedEffectError("LED package topology does not match this product")
    if not 1 <= frames <= LED_TICK_COUNT or not 1 <= colors <= LED_PALETTE_SIZE or flags != 0:
        raise LedEffectError("LED package header is invalid")
    payload = package[header_bytes:]
    if len(payload) != payload_bytes or zlib.crc32(payload) & 0xFFFFFFFF != expected_crc:
        raise LedEffectError("LED package checksum mismatch")
    expected_payload = colors * 3 + ticks + frames * frame_bytes
    if payload_bytes != expected_payload:
        raise LedEffectError("LED package payload size is invalid")
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": ticks,
        "unique_frame_count": frames,
        "palette_count": colors,
        "bytes": len(package),
        "crc32": f"{expected_crc:08x}",
        "sha256": hashlib.sha256(package).hexdigest(),
        "roles": {"primary": primary, "secondary": secondary, "accent": accent},
    }


def list_pixel_led_effects() -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    for path in sorted(led_effect_root().glob("*/manifest.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(value, dict) and value.get("effect_id"):
            effects.append(value)
    return effects


def load_pixel_led_effect(effect_id: str) -> dict[str, Any]:
    effect_id = _safe_effect_id(effect_id)
    path = led_effect_root() / effect_id / "manifest.json"
    if not path.is_file():
        raise LedEffectError(f"pixel LED effect not found: {effect_id}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LedEffectError(f"pixel LED effect manifest is invalid: {effect_id}")
    return value


def load_pixel_led_source(effect_id: str) -> dict[str, Any]:
    effect_id = _safe_effect_id(effect_id)
    path = led_effect_root() / effect_id / "source.json"
    if not path.is_file():
        raise LedEffectError(f"pixel LED effect source not found: {effect_id}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LedEffectError(f"pixel LED effect source is invalid: {effect_id}")
    return value


def load_pixel_led_package(effect_id: str) -> bytes:
    effect_id = _safe_effect_id(effect_id)
    path = led_effect_root() / effect_id / "effect.lef"
    if not path.is_file():
        raise LedEffectError(f"pixel LED effect package not found: {effect_id}")
    payload = path.read_bytes()
    inspect_led_package(payload)
    return payload


def save_pixel_led_effect(
    raw: dict[str, Any],
    *,
    effect_id: str,
    label: str,
    role: str,
    external_count: int = 0,
    external_used_bytes: int = 0,
) -> dict[str, Any]:
    effect_id = _safe_effect_id(effect_id)
    normalized_program, package = compile_led_program(raw.get("program"))
    existing = list_pixel_led_effects()
    current = next((item for item in existing if item.get("effect_id") == effect_id), None)
    if current is None and len(existing) + external_count >= MAX_CUSTOM_LED_EFFECTS:
        raise LedEffectError(f"maximum custom LED effects reached: {MAX_CUSTOM_LED_EFFECTS}")
    used_without_current = external_used_bytes + sum(
        int((item.get("package") or {}).get("bytes") or 0)
        for item in existing
        if item.get("effect_id") != effect_id
    )
    if used_without_current + len(package) > LED_EFFECT_BUDGET_BYTES:
        raise LedEffectError("custom LED effect storage budget exceeded")
    package_info = inspect_led_package(package)
    source = {
        "effect_id": effect_id,
        "label": label,
        "role": role,
        "program": normalized_program,
    }
    manifest = {
        "effect_id": effect_id,
        "label": label,
        "role": role,
        "source": "custom",
        "kind": "pixel_clip",
        "animated": True,
        "default_playback": "loop",
        "duration_ms": 3000,
        "program": {
            "version": 2,
            "type": "pixel_clip",
            "fps": LED_FPS,
            "frame_count": LED_TICK_COUNT,
            "palette": normalized_program["palette"],
            "roles": normalized_program["roles"],
        },
        "package": {**package_info, "filename": "effect.lef"},
        "sync": dict((current or {}).get("sync") or {"status": "not_synced"}),
        "parameter_schema": {
            "color": {"type": "string", "format": "color"},
            "secondary_color": {"type": "string", "format": "color"},
            "accent_color": {"type": "string", "format": "color"},
            "brightness": {"type": "integer", "minimum": 1, "maximum": 96, "default": 64},
            "intensity": {"type": "number", "minimum": 0.1, "maximum": 1.0, "default": 1.0},
        },
    }
    if current and (current.get("package") or {}).get("sha256") != package_info["sha256"]:
        manifest["sync"] = {"status": "not_synced"}
    directory = led_effect_root() / effect_id
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(directory / "source.json", _encoded_json(source))
    _atomic_write(directory / "effect.lef", package)
    _atomic_write(directory / "manifest.json", _encoded_json(manifest))
    return manifest


def update_pixel_led_sync(effect_id: str, *, status: str, device: Any = None) -> dict[str, Any]:
    effect_id = _safe_effect_id(effect_id)
    manifest = load_pixel_led_effect(effect_id)
    manifest["sync"] = {"status": status, "device": device}
    _atomic_write(led_effect_root() / effect_id / "manifest.json", _encoded_json(manifest))
    return manifest
