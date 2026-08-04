from __future__ import annotations

import importlib.util
import re
import struct
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from lampgo.expression_clips import LCD_HEIGHT, LCD_WIDTH, MAX_LCD_BYTES, load_expression_clip_lcd_payload
from lampgo.expression_library import expression_schemas, list_eyes, resolve_expression

ROOT = Path(__file__).resolve().parents[1]
AUTHORING_SCRIPT = ROOT / "tools/author_itachi_eye_clip.py"
APP_JS = ROOT / "lampgo/web/static/app.js"


def _authoring_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("itachi_eye_clip", AUTHORING_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reference_sprite() -> bytes:
    cv2 = pytest.importorskip("cv2")
    sprite = np.zeros((17 * LCD_HEIGHT, 10 * LCD_WIDTH, 3), dtype=np.uint8)
    for index in range(170):
        row, col = divmod(index, 10)
        x = col * LCD_WIDTH
        y = row * LCD_HEIGHT
        sprite[y + 70 : y + 103, x + 80 + index % 40 : x + 166 + index % 40] = (216, 0, 0)
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(sprite, cv2.COLOR_RGB2BGR))
    assert ok
    return bytes(encoded)


def test_itachi_authoring_filter_keeps_the_capcut_master_with_c6_safe_transfer() -> None:
    module = _authoring_module()
    filter_graph = module.c6_video_filter()

    assert "/Users/" not in AUTHORING_SCRIPT.read_text(encoding="utf-8")
    assert module.FRAME_COUNT == 170
    assert module.FPS == 30
    assert module.DURATION_MS == 5667
    assert (module.GRID_ROWS, module.GRID_COLS) == (17, 10)
    assert module.C6_VERTICAL_FLIP is False
    assert module.C6_CONTENT_FPS == 22
    assert "vflip" not in filter_graph
    assert "scale=320:172:flags=lanczos" in filter_graph
    assert "crop=" not in filter_graph
    assert "draw" not in filter_graph.lower()


def test_itachi_palette_removes_dark_codec_noise_but_keeps_bright_ink() -> None:
    module = _authoring_module()
    source = np.array(
        [
            [[89, 0, 0], [90, 60, 60], [150, 150, 150]],
            [[100, 85, 75], [110, 150, 150], [224, 0, 0]],
        ],
        dtype=np.uint8,
    )

    palette = module.c6_palette_transfer(source)

    assert palette.tolist() == [
        [[0, 0, 0], [224, 0, 0], [224, 224, 224]],
        [[0, 0, 0], [224, 224, 224], [224, 0, 0]],
    ]
    assert {tuple(pixel) for pixel in palette.reshape(-1, 3)} <= {
        (0, 0, 0),
        (224, 0, 0),
        (224, 224, 224),
    }


def test_itachi_temporal_hold_keeps_30fps_envelope_and_both_endpoints() -> None:
    module = _authoring_module()
    source_indices = [module.c6_source_frame_index(index) for index in range(module.FRAME_COUNT)]

    assert source_indices[0] == 0
    assert source_indices[-1] == module.FRAME_COUNT - 1
    assert source_indices == sorted(source_indices)
    assert len(set(source_indices)) == 125
    with pytest.raises(ValueError):
        module.c6_source_frame_index(module.FRAME_COUNT)


def test_eye_schema_matches_the_c6_id_30fps_and_six_second_contract() -> None:
    schema = expression_schemas()["eye_clip"]["properties"]

    assert re.fullmatch(schema["eye_clip_id"]["pattern"], "itachi-eyes")
    assert not re.fullmatch(schema["eye_clip_id"]["pattern"], "itachi-sharingan")
    assert schema["fps"] == {"type": "integer", "minimum": 8, "maximum": 30}
    assert schema["duration_ms"] == {"type": "integer", "minimum": 1000, "maximum": 6000}


def test_expression_preview_uses_the_selected_eye_duration_instead_of_three_seconds() -> None:
    source = APP_JS.read_text(encoding="utf-8")

    assert "function eyeDurationMs(eyeId)" in source
    assert "duration_ms: eyeDurationMs(eyeClipId)" in source
    assert "looping ? elapsed % durationMs : Math.min(elapsed, durationMs - 1)" in source
    assert "looping ? elapsed % ledDurationMs : Math.min(elapsed, ledDurationMs - 1)" in source


def test_itachi_eye_clip_installs_a_direct_video_sprite_and_preview(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    module = _authoring_module()
    source_video = tmp_path / "preview_2.mov"

    def fake_render_reference_assets(*, video_path: Path, output_dir: Path) -> tuple[Path, Path]:
        assert video_path == source_video
        sprite = output_dir / module.SOURCE_FILENAME
        preview = output_dir / module.PREVIEW_FILENAME
        sprite.write_bytes(_reference_sprite())
        preview.write_bytes(b"local preview fixture")
        return sprite, preview

    monkeypatch.setattr(module, "render_reference_assets", fake_render_reference_assets)
    manifest = module.create_itachi_eye_clip(video_path=source_video)
    payload = load_expression_clip_lcd_payload(module.CLIP_ID)
    width, height, frame_count, fps = struct.unpack_from("<HHHH", payload, 6)

    assert manifest["clip_id"] == module.CLIP_ID
    assert manifest["default_led_effect_id"] is None
    assert manifest["source"]["grid_rows"] == 17
    assert manifest["source"]["grid_cols"] == 10
    assert manifest["sync"]["status"] == "unsynced"
    assert (width, height, frame_count, fps) == (LCD_WIDTH, LCD_HEIGHT, 170, 30)
    assert len(payload) <= MAX_LCD_BYTES
    preview = tmp_path / "expression_clips" / module.CLIP_ID / module.PREVIEW_FILENAME
    assert preview.read_bytes() == b"local preview fixture"
    assert any(eye["eye_clip_id"] == module.CLIP_ID for eye in list_eyes())
    resolved = resolve_expression({"eye_clip_id": module.CLIP_ID})
    assert resolved["duration_ms"] == 5667
    assert resolved["led_effect_id"] is None
