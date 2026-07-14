"""LCD expression clip asset pipeline for device micro-expressions."""

from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from lampgo import personastore

TARGET_DURATION_S = 3.0
MIN_DURATION_S = 2.5
MAX_DURATION_S = 3.5
MIN_FPS = 8
MAX_FPS = 12
DEFAULT_FPS = 10
MAX_CLIPS = 10
MAX_LCD_BYTES = 256 * 1024

LCD_WIDTH = 320
LCD_HEIGHT = 172

LCD_MAGIC = b"LGLCD1"

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


class ExpressionClipError(ValueError):
    """Raised when a source asset cannot become a device-safe expression clip."""


@dataclass(frozen=True)
class ExpressionClipPackage:
    clip_id: str
    expression: str
    fps: int
    duration_ms: int
    frame_count: int
    source_filename: str
    source_content_type: str
    lcd_bytes: int
    lcd_sha256: str
    led_effect: str
    default_led_effect_id: str | None
    source_stored_filename: str
    grid_rows: int | None
    grid_cols: int | None
    path: Path

    def to_manifest(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "expression": self.expression,
            "fps": self.fps,
            "duration_ms": self.duration_ms,
            "frame_count": self.frame_count,
            "source_filename": self.source_filename,
            "source_content_type": self.source_content_type,
            "kind": "eye_clip",
            "eye_clip_id": self.clip_id,
            "default_led_effect_id": self.default_led_effect_id,
            "source": {
                "filename": self.source_filename,
                "stored_filename": self.source_stored_filename,
                "content_type": self.source_content_type,
                "grid_rows": self.grid_rows,
                "grid_cols": self.grid_cols,
            },
            "lcd": {
                "width": LCD_WIDTH,
                "height": LCD_HEIGHT,
                "bytes": self.lcd_bytes,
                "sha256": self.lcd_sha256,
                "filename": "lcd.bin",
            },
            "led": {
                "type": "procedural",
                "effect": self.led_effect,
            },
            "sync": {
                "status": "unsynced",
                "last_synced_at": None,
                "device": None,
            },
        }


def expression_clip_root() -> Path:
    root = personastore.lampgo_home() / "expression_clips"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sanitize_clip_id(value: str) -> str:
    clip_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-_").lower()
    if not clip_id:
        raise ExpressionClipError("clip_id is required")
    if not _SAFE_ID_RE.match(clip_id):
        raise ExpressionClipError("clip_id must be 1-32 chars: letters, numbers, dash, underscore")
    return clip_id


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clip_dir(clip_id: str) -> Path:
    return expression_clip_root() / sanitize_clip_id(clip_id)


def _manifest_path(clip_id: str) -> Path:
    return _clip_dir(clip_id) / "manifest.json"


def list_expression_clips() -> list[dict[str, Any]]:
    clips: list[dict[str, Any]] = []
    root = expression_clip_root()
    for manifest in sorted(root.glob("*/manifest.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            data.setdefault("path", str(manifest.parent))
            clips.append(data)
    return clips


def clip_for_expression(expression: str) -> dict[str, Any] | None:
    wanted = expression.strip().lower()
    if not wanted:
        return None
    for clip in list_expression_clips():
        if str(clip.get("expression") or "").strip().lower() == wanted:
            return clip
    return None


def load_expression_clip(clip_id: str) -> dict[str, Any]:
    path = _manifest_path(clip_id)
    if not path.exists():
        raise ExpressionClipError(f"expression clip not found: {clip_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ExpressionClipError(f"invalid manifest for clip: {clip_id}")
    return data


def load_expression_clip_lcd_payload(clip_id: str) -> bytes:
    manifest = load_expression_clip(clip_id)
    lcd_meta = manifest.get("lcd") or {}
    filename = str(lcd_meta.get("filename") or "lcd.bin")
    path = _clip_dir(clip_id) / filename
    if not path.exists():
        raise ExpressionClipError(f"lcd payload not found for clip: {clip_id}")
    payload = path.read_bytes()
    expected_size = int(lcd_meta.get("bytes") or 0)
    expected_sha = str(lcd_meta.get("sha256") or "")
    if expected_size and len(payload) != expected_size:
        raise ExpressionClipError(f"lcd payload size mismatch for clip: {clip_id}")
    if expected_sha and _sha256(payload) != expected_sha:
        raise ExpressionClipError(f"lcd payload sha mismatch for clip: {clip_id}")
    return payload


def update_expression_clip_sync(clip_id: str, *, status: str, device: dict[str, Any] | None = None) -> dict[str, Any]:
    import time

    path = _manifest_path(clip_id)
    manifest = load_expression_clip(clip_id)
    sync = dict(manifest.get("sync") or {})
    sync.update(
        {
            "status": status,
            "last_synced_at": time.time() if status == "synced" else sync.get("last_synced_at"),
            "device": device,
        }
    )
    manifest["sync"] = sync
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def build_sync_chunks(clip_id: str, *, chunk_size: int = 64) -> list[dict[str, Any]]:
    """Return small JSON-safe chunks for S3 sync and UART relay to C6."""
    manifest = load_expression_clip(clip_id)
    clip_dir = _clip_dir(clip_id)
    chunks: list[dict[str, Any]] = [
        {
            "action": "begin",
            "clip_id": manifest["clip_id"],
            "expression": manifest["expression"],
            "fps": manifest["fps"],
            "duration_ms": manifest["duration_ms"],
            "frame_count": manifest["frame_count"],
            "lcd_bytes": manifest["lcd"]["bytes"],
            "lcd_sha256": manifest["lcd"]["sha256"],
            "led_effect": (manifest.get("led") or {}).get("effect") or manifest["expression"],
        }
    ]

    payload = (clip_dir / "lcd.bin").read_bytes()
    for offset in range(0, len(payload), chunk_size):
        part = payload[offset : offset + chunk_size]
        chunks.append(
            {
                "action": "chunk",
                "clip_id": manifest["clip_id"],
                "target": "lcd",
                "offset": offset,
                "data": part.hex(),
            }
        )
    chunks.append({"action": "commit", "clip_id": manifest["clip_id"]})
    return chunks


def create_expression_clip(
    *,
    clip_id: str,
    expression: str,
    source_bytes: bytes,
    filename: str,
    content_type: str = "",
    fps: int = DEFAULT_FPS,
    duration_s: float | None = None,
    grid_rows: int | None = None,
    grid_cols: int | None = None,
    default_led_effect_id: str | None = None,
) -> dict[str, Any]:
    clip_id = sanitize_clip_id(clip_id)
    expression = (expression or clip_id).strip().lower()
    if not expression:
        raise ExpressionClipError("expression is required")
    if len(list_expression_clips()) >= MAX_CLIPS and not _manifest_path(clip_id).exists():
        raise ExpressionClipError(f"maximum expression clips reached: {MAX_CLIPS}")
    if fps < MIN_FPS or fps > MAX_FPS:
        raise ExpressionClipError(f"fps must be between {MIN_FPS} and {MAX_FPS}")
    if not source_bytes:
        raise ExpressionClipError("source file is empty")

    frames = _decode_source_frames(
        source_bytes,
        filename=filename,
        grid_rows=grid_rows,
        grid_cols=grid_cols,
    )
    if not frames:
        raise ExpressionClipError("no frames decoded from source")

    actual_duration_s = duration_s if duration_s is not None else len(frames) / float(fps)
    if actual_duration_s < MIN_DURATION_S or actual_duration_s > MAX_DURATION_S:
        raise ExpressionClipError(
            f"duration must be {MIN_DURATION_S:.1f}-{MAX_DURATION_S:.1f}s; got {actual_duration_s:.2f}s"
        )

    duration_ms = int(round(actual_duration_s * 1000))
    lcd_payload = _encode_lcd_package(frames, fps=fps, duration_ms=duration_ms)
    if len(lcd_payload) > MAX_LCD_BYTES:
        raise ExpressionClipError(
            "converted LCD clip exceeds device cache budget "
            f"(lcd={len(lcd_payload)} bytes)"
        )

    clip_dir = _clip_dir(clip_id)
    clip_dir.mkdir(parents=True, exist_ok=True)
    source_suffix = Path(filename or "source.bin").suffix or ".bin"
    source_stored_filename = f"source{source_suffix}"
    (clip_dir / source_stored_filename).write_bytes(source_bytes)
    (clip_dir / "lcd.bin").write_bytes(lcd_payload)
    stale_led = clip_dir / "led.bin"
    if stale_led.exists():
        stale_led.unlink()

    package = ExpressionClipPackage(
        clip_id=clip_id,
        expression=expression,
        fps=fps,
        duration_ms=duration_ms,
        frame_count=len(frames),
        source_filename=filename or "upload",
        source_content_type=content_type,
        lcd_bytes=len(lcd_payload),
        lcd_sha256=_sha256(lcd_payload),
        led_effect=expression,
        default_led_effect_id=(default_led_effect_id or "").strip().lower() or None,
        source_stored_filename=source_stored_filename,
        grid_rows=grid_rows,
        grid_cols=grid_cols,
        path=clip_dir,
    )
    manifest = package.to_manifest()
    (clip_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _decode_source_frames(
    source_bytes: bytes,
    *,
    filename: str,
    grid_rows: int | None,
    grid_cols: int | None,
) -> list[np.ndarray]:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".zip":
        return _decode_zip_frames(source_bytes)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        image = _decode_image(source_bytes)
        if grid_rows and grid_cols:
            return _slice_sprite_sheet(image, rows=grid_rows, cols=grid_cols)
        return [image]
    if suffix in {".gif", ".mp4", ".mov", ".m4v", ".avi", ".webm"}:
        return _decode_video_frames(source_bytes, suffix=suffix)
    raise ExpressionClipError(f"unsupported source type: {suffix or filename or 'unknown'}")


def _require_cv2():
    try:
        import cv2
    except Exception as exc:  # pragma: no cover - exact ImportError varies by platform
        raise ExpressionClipError("opencv-python is required for expression clip conversion") from exc
    return cv2


def _decode_image(source_bytes: bytes) -> np.ndarray:
    cv2 = _require_cv2()
    data = np.frombuffer(source_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ExpressionClipError("image decode failed")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _decode_video_frames(source_bytes: bytes, *, suffix: str) -> list[np.ndarray]:
    cv2 = _require_cv2()
    frames: list[np.ndarray] = []
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(source_bytes)
        tmp.flush()
        cap = cv2.VideoCapture(tmp.name)
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        finally:
            cap.release()
    return frames


def _decode_zip_frames(source_bytes: bytes) -> list[np.ndarray]:
    frames: list[np.ndarray] = []
    with zipfile.ZipFile(io.BytesIO(source_bytes)) as zf:
        names = sorted(
            name
            for name in zf.namelist()
            if Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} and not name.endswith("/")
        )
        for name in names:
            frames.append(_decode_image(zf.read(name)))
    return frames


def _slice_sprite_sheet(image: np.ndarray, *, rows: int, cols: int) -> list[np.ndarray]:
    if rows <= 0 or cols <= 0:
        raise ExpressionClipError("grid_rows and grid_cols must be positive")
    height, width = image.shape[:2]
    cell_w = width // cols
    cell_h = height // rows
    if cell_w <= 0 or cell_h <= 0:
        raise ExpressionClipError("sprite sheet grid is larger than the source image")
    frames: list[np.ndarray] = []
    for row in range(rows):
        for col in range(cols):
            frame = image[row * cell_h : (row + 1) * cell_h, col * cell_w : (col + 1) * cell_w]
            frames.append(frame.copy())
    return frames


def _rgb_to_rgb565(frame: np.ndarray) -> np.ndarray:
    rgb = frame.astype(np.uint16)
    red = (rgb[:, :, 0] >> 3) << 11
    green = (rgb[:, :, 1] >> 2) << 5
    blue = rgb[:, :, 2] >> 3
    return (red | green | blue).astype(np.uint16)


def _resize_rgb(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    cv2 = _require_cv2()
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)


def _encode_lcd_package(frames: list[np.ndarray], *, fps: int, duration_ms: int) -> bytes:
    out = bytearray()
    out += LCD_MAGIC
    out += struct.pack("<HHHH", LCD_WIDTH, LCD_HEIGHT, len(frames), fps)
    previous: np.ndarray | None = None
    frame_duration_ms = max(1, int(round(duration_ms / len(frames))))
    for frame in frames:
        rgb565 = _rgb_to_rgb565(_resize_rgb(frame, LCD_WIDTH, LCD_HEIGHT))
        if previous is None:
            x0, y0, x1, y1 = 0, 0, LCD_WIDTH - 1, LCD_HEIGHT - 1
        else:
            changed = rgb565 != previous
            if not np.any(changed):
                x0, y0, x1, y1 = 0, 0, 1, 1
            else:
                ys, xs = np.where(changed)
                x0, x1 = int(xs.min()), int(xs.max())
                y0, y1 = int(ys.min()), int(ys.max())
        patch = rgb565[y0 : y1 + 1, x0 : x1 + 1]
        runs = _rle_rgb565(patch.reshape(-1))
        out += struct.pack("<HHHHHH", x0, y0, patch.shape[1], patch.shape[0], frame_duration_ms, len(runs))
        for count, color in runs:
            out += struct.pack("<HH", count, color)
        previous = rgb565
    return bytes(out)


def _rle_rgb565(values: np.ndarray) -> list[tuple[int, int]]:
    if values.size == 0:
        return []
    runs: list[tuple[int, int]] = []
    current = int(values[0])
    count = 0
    for value in values:
        value_int = int(value)
        if value_int == current and count < 65535:
            count += 1
            continue
        runs.append((count, current))
        current = value_int
        count = 1
    runs.append((count, current))
    return runs
