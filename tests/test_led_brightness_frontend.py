from pathlib import Path


def test_frontend_exposes_quick_led_brightness_levels_and_status():
    html = Path("lampgo/web/static/index.html").read_text(encoding="utf-8")

    assert 'id="esp32-led-brightness-slider"' in html
    assert 'id="esp32-led-brightness-value"' in html
    assert 'id="esp32-led-brightness-status"' in html
    for level in (8, 16, 24, 32):
        assert f'data-led-brightness-quick="{level}"' in html


def test_frontend_brightness_uses_server_config_and_serial_latest_value_sync():
    source = Path("lampgo/web/static/app.js").read_text(encoding="utf-8")

    assert 'const resp = await fetch("/api/config");' in source
    assert 'sections.device_esp32["device_esp32.led_brightness"]' in source
    assert 'body: JSON.stringify({ "device_esp32.led_brightness": level })' in source
    assert "esp32LedBrightnessDesired = level;" in source
    assert "while (esp32LedBrightnessDesired != null)" in source
    assert 'setEsp32LedBrightnessStatus("error"' in source
