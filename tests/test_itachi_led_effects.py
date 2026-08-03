from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from lampgo.led_effects import (
    LED_TICK_COUNT,
    MAX_LED_EFFECT_BYTES,
    compile_led_program,
    inspect_led_package,
    load_pixel_led_source,
)

ROOT = Path(__file__).resolve().parents[1]
AUTHORING_SCRIPT = ROOT / "tools/author_itachi_led_effects.py"


def _authoring_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("itachi_led_effects", AUTHORING_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_itachi_led_effects_compile_as_three_second_one_shots() -> None:
    module = _authoring_module()
    effects = module.build_effects()

    assert [effect["effect_id"] for effect in effects] == ["itachi-awaken", "itachi-dual", "amaterasu"]
    for effect in effects:
        normalized, package = compile_led_program(effect["program"])
        info = inspect_led_package(package)
        assert effect["role"] == "accent"
        assert effect["default_playback"] == "once"
        assert sum(frame["ticks"] for frame in normalized["frames"]) == LED_TICK_COUNT
        assert info["frame_count"] == LED_TICK_COUNT
        assert info["bytes"] <= MAX_LED_EFFECT_BYTES


def test_dual_sharingan_effect_has_two_open_eyes_and_a_clear_center_gap() -> None:
    module = _authoring_module()
    effect = next(effect for effect in module.build_effects() if effect["effect_id"] == "itachi-dual")
    final_rows = effect["program"]["frames"][8]["rows"]

    assert len(effect["effect_id"]) <= 13
    assert any(cell != "." for row in final_rows for cell in row[5:24])
    assert any(cell != "." for row in final_rows for cell in row[27:46])
    assert all(row[x] == "." for row in final_rows for x in range(24, 27))


def test_itachi_led_effect_install_stays_local_and_editable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path))
    module = _authoring_module()

    saved = module.install_effects()

    assert [effect["sync"]["status"] for effect in saved] == ["not_synced", "not_synced", "not_synced"]
    for effect in saved:
        source = load_pixel_led_source(effect["effect_id"])
        assert source["label"] == effect["label"]
        assert source["default_playback"] == "once"
        assert sum(frame["ticks"] for frame in source["program"]["frames"]) == LED_TICK_COUNT
