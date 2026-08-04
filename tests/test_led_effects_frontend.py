from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "lampgo/web/static/app.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "lampgo/web/static/style.css").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "lampgo/web/static/index.html").read_text(encoding="utf-8")


def test_websocket_prefers_full_led_effect_catalog() -> None:
    assert "const hasFullLedEffectCatalog = Array.isArray(msg.result.led_effects);" in APP_JS
    assert "? ledEffectExpressionEntries(msg.result.led_effects)" in APP_JS
    assert "renderExpressions(ledEffectExpressionEntries(expressionLedEffects));" in APP_JS


def test_led_effects_are_grouped_with_added_effects_first() -> None:
    assert "const groups = { added: [], factory: [] };" in APP_JS
    assert 'effect && effect.source !== "builtin" ? "added" : "factory"' in APP_JS
    added = APP_JS.index('{ id: "added", label: "后续新增", names: groups.added }')
    factory = APP_JS.index('{ id: "factory", label: "出厂默认", names: groups.factory }')
    assert added < factory
    assert (
        'heading.className = `expression-effect-group-title${separated ? " expression-group-divider" : ""}`;'
        in APP_JS
    )
    assert "appendGroupHeading(group, index > 0);" in APP_JS
    assert 'card.classList.add("expression-effect-card--custom");' in APP_JS
    assert ".expression-effect-card--custom .expression-card-actions" in STYLE_CSS


def test_led_effect_group_divider_is_a_single_thin_line() -> None:
    assert ".expression-effect-group-title.expression-group-divider" in STYLE_CSS
    assert "border-top: 1px solid #dce3e7;" in STYLE_CSS
    assert "feature-batch-20260803" in INDEX_HTML


def test_led_editor_preserves_one_shot_default_playback() -> None:
    assert 'const ledEffectDefaultPlayback = document.getElementById("led-effect-default-playback");' in APP_JS
    assert 'default_playback: (ledEffectDefaultPlayback && ledEffectDefaultPlayback.value) || "loop",' in APP_JS
    assert 'source.default_playback === "once" ? "once" : "loop"' in APP_JS
    assert 'playback: (effect && effect.default_playback) || "loop",' in APP_JS
    assert 'id="led-effect-default-playback"' in INDEX_HTML
