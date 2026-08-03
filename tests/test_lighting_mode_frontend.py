from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "lampgo/web/static/app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "lampgo/web/static/index.html").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "lampgo/web/static/style.css").read_text(encoding="utf-8")


def test_frontend_has_a_single_lighting_mode_toggle() -> None:
    assert 'id="btn-lighting-mode"' in INDEX_HTML
    assert 'id="lighting-mode-label"' in INDEX_HTML
    assert 'aria-pressed="false"' in INDEX_HTML
    assert 'skillId = exiting ? "exit_lighting_mode" : "enter_lighting_mode";' in APP_JS
    assert 'btnLightingMode.addEventListener("click", toggleLightingMode);' in APP_JS


def test_frontend_reflects_backend_mode_state_and_return_safe_completion() -> None:
    assert "setLightingModeState(data.lighting_mode || {});" in APP_JS
    assert 'data.return_safe_invoked ? "已退出照明模式并回到安全位"' in APP_JS
    assert ".lighting-mode-button.is-active" in STYLE_CSS
    assert ".lighting-mode-button.is-pending" in STYLE_CSS


def test_frontend_detects_when_the_lighting_request_cannot_be_sent() -> None:
    assert "ws.send(JSON.stringify(obj));\n      return true;" in APP_JS
    assert "return false;\n  }\n\n  function handleMessage" in APP_JS
    assert 'if (!send({ type: "invoke", skill_id: skillId' in APP_JS
