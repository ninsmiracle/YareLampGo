#!/usr/bin/env python3
"""Build the Itachi eye clip directly from the supplied reference movie.

This is deliberately a video conversion tool, not an illustration tool.  It
uses ffmpeg to scale the user-authored CapCut master to the C6's 320x172 LCD,
then applies a stable low-colour transfer needed by the C6's 256 KiB clip
cache.  No eye geometry, Sharingan, or blood is redrawn by this script.

The source is retained as a 10x17 PNG sprite for the existing local web
preview.  A matching MP4 preview is also installed beside the clip.  Neither
operation contacts or transfers anything to the S3 or C6.

Usage:
    uv run python tools/author_itachi_eye_clip.py --video /path/to/preview.mov --install
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from lampgo.expression_clips import (
    MAX_LCD_BYTES,
    create_expression_clip,
    expression_clip_root,
)
from lampgo.expression_library import refresh_llm_expression_catalog

CLIP_ID = "itachi-eyes"
EXPRESSION_LABEL = "写轮眼·开眼"
DEFAULT_LED_EFFECT_ID = None
FPS = 30
DURATION_MS = 5667
FRAME_COUNT = 170
GRID_COLS = 10
GRID_ROWS = 17
SOURCE_FILENAME = "itachi_capcut_preview2_10x17_320x172.png"
PREVIEW_FILENAME = "preview.mp4"

# The CapCut master is 2010x1080, essentially the same aspect ratio as the
# C6 display (320x172).  We keep the full 320x172 picture so the user's
# CapCut-adjusted composition is not cropped or repositioned.

# ``preview_2.mov`` is already upright in the C6's landscape coordinate space.
# A physical C6 check confirmed that a further vertical flip puts the opening
# shape on the lower half of the panel, so this CapCut master must be packaged
# without ``vflip``.
C6_VERTICAL_FLIP = False

# The encoder is delta-RLE rather than video-compressed.  CapCut's H.264 master
# has faint, rapidly changing colour noise in the black background, so direct
# posterization reaches ~700 KiB.  A fixed black/bright-red/bright-white
# palette removes that noise while preserving the deliberately brighter
# Sharingan opening in a full-size C6 package.
C6_RED_MIN = 90
C6_RED_SEPARATION = 30
C6_WHITE_MIN = 120
C6_INK = 224

# The panel still receives 30 scheduled frames per second.  Holding a few
# neighbouring C6 frames at a 22 fps source cadence removes otherwise invisible
# H.264/RLE churn from the brighter red treatment and leaves safe cache margin.
C6_CONTENT_FPS = 22


def c6_video_filter() -> str:
    """Return the exact CapCut-master conversion filter used for C6."""
    orientation = "vflip," if C6_VERTICAL_FLIP else ""
    return (
        f"fps={FPS},"
        f"{orientation}scale=320:172:flags=lanczos,setsar=1"
    )


def c6_palette_transfer(frame_rgb: np.ndarray) -> np.ndarray:
    """Reduce CapCut pixels to the stable C6 black/red/white ink palette."""
    if frame_rgb.ndim != 3 or frame_rgb.shape[2] != 3:
        raise ValueError("C6 palette transfer expects an RGB image")

    source = frame_rgb.astype(np.int16, copy=False)
    red, green, blue = source[:, :, 0], source[:, :, 1], source[:, :, 2]
    maximum = source.max(axis=2)
    output = np.zeros_like(source, dtype=np.uint8)

    red_ink = (
        (red >= C6_RED_MIN)
        & (red - green >= C6_RED_SEPARATION)
        & (red - blue >= C6_RED_SEPARATION)
    )
    output[red_ink] = (C6_INK, 0, 0)
    white_ink = (~red_ink) & (maximum >= C6_WHITE_MIN)
    output[white_ink] = (C6_INK, C6_INK, C6_INK)
    return output


def apply_c6_palette_to_sprite(sprite_path: Path) -> None:
    """Replace the rendered sprite with the C6 palette version in place."""
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - installation error path
        raise RuntimeError("opencv-python is required to prepare the C6 sprite") from exc

    image_bgr = cv2.imread(str(sprite_path), cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise RuntimeError(f"could not read rendered C6 sprite: {sprite_path}")
    expected_shape = (GRID_ROWS * 172, GRID_COLS * 320, 3)
    if image_bgr.shape != expected_shape:
        raise RuntimeError(f"unexpected C6 sprite shape: {image_bgr.shape}, expected {expected_shape}")
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    palette_bgr = cv2.cvtColor(c6_palette_transfer(image_rgb), cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(str(sprite_path), palette_bgr):
        raise RuntimeError(f"could not save C6 palette sprite: {sprite_path}")


def c6_source_frame_index(output_index: int) -> int:
    """Map a 30 fps C6 frame to a nearby 22 fps source frame, including the end."""
    if not 0 <= output_index < FRAME_COUNT:
        raise ValueError(f"output frame must be 0-{FRAME_COUNT - 1}")
    content_index = round(output_index * C6_CONTENT_FPS / FPS)
    return min(FRAME_COUNT - 1, int(round(content_index * FPS / C6_CONTENT_FPS)))


def apply_c6_temporal_hold_to_sprite(sprite_path: Path) -> None:
    """Keep C6 transport at 30 fps while reducing the source-change cadence."""
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - installation error path
        raise RuntimeError("opencv-python is required to prepare the C6 sprite") from exc

    sprite_bgr = cv2.imread(str(sprite_path), cv2.IMREAD_COLOR)
    if sprite_bgr is None:
        raise RuntimeError(f"could not read rendered C6 sprite: {sprite_path}")
    expected_shape = (GRID_ROWS * 172, GRID_COLS * 320, 3)
    if sprite_bgr.shape != expected_shape:
        raise RuntimeError(f"unexpected C6 sprite shape: {sprite_bgr.shape}, expected {expected_shape}")

    source = sprite_bgr.copy()
    for output_index in range(FRAME_COUNT):
        source_index = c6_source_frame_index(output_index)
        output_row, output_col = divmod(output_index, GRID_COLS)
        source_row, source_col = divmod(source_index, GRID_COLS)
        y0, x0 = output_row * 172, output_col * 320
        source_y0, source_x0 = source_row * 172, source_col * 320
        sprite_bgr[y0 : y0 + 172, x0 : x0 + 320] = source[
            source_y0 : source_y0 + 172,
            source_x0 : source_x0 + 320,
        ]
    if not cv2.imwrite(str(sprite_path), sprite_bgr):
        raise RuntimeError(f"could not save C6 temporal sprite: {sprite_path}")


def _run_ffmpeg(arguments: Sequence[str]) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to build the Itachi reference clip")
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "ffmpeg failed").strip()
        raise RuntimeError(f"ffmpeg conversion failed: {detail}")


def render_reference_assets(*, video_path: Path, output_dir: Path) -> tuple[Path, Path]:
    """Render a C6 sprite plus a locally playable preview from ``video_path``."""
    if not video_path.is_file():
        raise FileNotFoundError(f"Itachi reference video not found: {video_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    sprite_path = output_dir / SOURCE_FILENAME
    preview_path = output_dir / PREVIEW_FILENAME
    filter_graph = c6_video_filter()
    _run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-vf",
            f"{filter_graph},tile={GRID_COLS}x{GRID_ROWS}:padding=0:margin=0",
            "-frames:v",
            "1",
            str(sprite_path),
        ]
    )
    apply_c6_palette_to_sprite(sprite_path)
    apply_c6_temporal_hold_to_sprite(sprite_path)
    _run_ffmpeg(
        [
            "-framerate",
            str(FPS),
            "-loop",
            "1",
            "-i",
            str(sprite_path),
            "-vf",
            f"untile={GRID_COLS}x{GRID_ROWS},setpts=N/({FPS}*TB),fps={FPS},setsar=1",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "15",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-frames:v",
            str(FRAME_COUNT),
            str(preview_path),
        ]
    )
    return sprite_path, preview_path


def create_itachi_eye_clip(*, video_path: Path) -> dict[str, object]:
    """Replace the local clip with a capacity-checked direct-video conversion."""
    with tempfile.TemporaryDirectory(prefix="lampgo-itachi-") as temporary:
        sprite_path, preview_path = render_reference_assets(
            video_path=video_path,
            output_dir=Path(temporary),
        )
        manifest = create_expression_clip(
            clip_id=CLIP_ID,
            expression=EXPRESSION_LABEL,
            source_bytes=sprite_path.read_bytes(),
            filename=SOURCE_FILENAME,
            content_type="image/png",
            fps=FPS,
            duration_s=DURATION_MS / 1000,
            grid_rows=GRID_ROWS,
            grid_cols=GRID_COLS,
            default_led_effect_id=DEFAULT_LED_EFFECT_ID,
        )
        if int(manifest["frame_count"]) != FRAME_COUNT:
            raise RuntimeError(
                f"reference frame count changed: expected {FRAME_COUNT}, got {manifest['frame_count']}"
            )
        if int(manifest["lcd"]["bytes"]) > MAX_LCD_BYTES:
            raise RuntimeError("reference conversion exceeded the C6 cache budget")
        shutil.copy2(preview_path, expression_clip_root() / CLIP_ID / PREVIEW_FILENAME)

    refresh_llm_expression_catalog()
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="save the local sprite, preview, and C6 package")
    parser.add_argument(
        "--video",
        type=Path,
        help="source movie exported from the editor; required together with --install",
    )
    args = parser.parse_args()
    if not args.install:
        print(
            f"configured {CLIP_ID}: {FRAME_COUNT} frames, {FPS} fps, {DURATION_MS} ms; "
            "dry run only"
        )
        return 0
    if args.video is None:
        parser.error("--video is required together with --install")
    manifest = create_itachi_eye_clip(video_path=args.video.expanduser().resolve())
    preview = expression_clip_root() / CLIP_ID / PREVIEW_FILENAME
    print(
        f"installed {manifest['clip_id']}: {manifest['lcd']['bytes']} bytes, "
        f"{manifest['frame_count']} frames, preview={preview}, sync={manifest['sync']['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
