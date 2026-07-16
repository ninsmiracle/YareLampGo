from __future__ import annotations

import json

import pytest

import lampgo.expression_library as expression_library
import lampgo.led_effects as led_effects
from lampgo.expression_library import (
    ExpressionLibraryError,
    expression_capabilities,
    refresh_llm_expression_catalog,
    save_led_effect,
)
from lampgo.led_effects import (
    LED_FRAME_BYTES,
    LEF_HEADER,
    LedEffectError,
    compile_led_program,
    inspect_led_package,
    load_pixel_led_package,
    load_pixel_led_source,
)


def _rows(*, offset: int = 0, symbol: str = "1") -> list[str]:
    rows = [list("." * 51) for _ in range(9)]
    for col in range(12 + offset, 39 + offset):
        if 0 <= col < 51:
            distance = abs(col - (25 + offset))
            row = min(8, 3 + (distance * distance) // 190)
            rows[row][col] = symbol
    return ["".join(row) for row in rows]


def _effect(effect_id: str = "rainbow_smile") -> dict:
    return {
        "effect_id": effect_id,
        "label": "彩虹笑嘴",
        "role": "mouth",
        "program": {
            "version": 2,
            "type": "pixel_clip",
            "fps": 10,
            "palette": {".": "#000000", "1": "#ff0066", "2": "#00d8ff"},
            "roles": {"primary": "1", "secondary": "2"},
            "frames": [
                {"rows": _rows(symbol="1"), "ticks": 10},
                {"rows": _rows(offset=1, symbol="2"), "ticks": 10},
                {"rows": _rows(symbol="1"), "ticks": 10},
            ],
        },
    }


def test_lef1_compile_is_deterministic_and_deduplicates_frames():
    normalized, first = compile_led_program(_effect()["program"])
    _, second = compile_led_program(_effect()["program"])
    info = inspect_led_package(first)

    assert first == second
    assert normalized["fps"] == 10
    assert info["frame_count"] == 30
    assert info["unique_frame_count"] == 2
    assert info["palette_count"] == 3
    assert info["bytes"] == LEF_HEADER.size + 3 * 3 + 30 + 2 * LED_FRAME_BYTES
    assert info["roles"] == {"primary": 1, "secondary": 2, "accent": 255}


def test_lef1_rejects_unsafe_or_incomplete_programs():
    bad = _effect()["program"]
    bad["frames"][0]["rows"][0] = "." * 50
    with pytest.raises(LedEffectError, match="51"):
        compile_led_program(bad)

    bad = _effect()["program"]
    bad["frames"] = [{"rows": _rows(), "ticks": 29}]
    with pytest.raises(LedEffectError, match="exactly 30"):
        compile_led_program(bad)


def test_pixel_effect_storage_capacity_and_llm_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    saved = save_led_effect(_effect())

    assert saved["kind"] == "pixel_clip"
    assert saved["default_playback"] == "loop"
    assert saved["package"]["bytes"] < 8 * 1024
    assert load_pixel_led_package("rainbow_smile")
    assert load_pixel_led_source("rainbow_smile")["program"]["frames"][0]["ticks"] == 10
    assert expression_capabilities()["led_effects"]["installed_custom"] == 1

    path = refresh_llm_expression_catalog()
    catalog = json.loads(path.read_text(encoding="utf-8"))
    assert catalog["schema_version"] == 1
    assert catalog["revision"] >= 2
    assert any(item["effect_id"] == "rainbow_smile" for item in catalog["led_effects"])


def test_custom_effect_limit_is_shared_with_legacy_templates(monkeypatch, tmp_path):
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    monkeypatch.setattr(expression_library, "MAX_CUSTOM_LED_EFFECTS", 1)
    monkeypatch.setattr(led_effects, "MAX_CUSTOM_LED_EFFECTS", 1)
    save_led_effect(
        {
            "effect_id": "legacy_mouth",
            "role": "mouth",
            "program": {"version": 1, "template": "mouth", "variant": "smile"},
        }
    )

    with pytest.raises(ExpressionLibraryError, match="maximum custom LED effects"):
        save_led_effect(_effect())
