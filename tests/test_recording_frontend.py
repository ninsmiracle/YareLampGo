from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "lampgo/web/static/app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "lampgo/web/static/index.html").read_text(encoding="utf-8")


def test_recording_button_opens_a_start_dialog_with_inline_feedback() -> None:
    assert 'id="btn-record-motion-panel"' in INDEX_HTML
    assert 'id="record-start-dialog"' in INDEX_HTML
    assert 'id="record-start-error"' in INDEX_HTML
    assert 'role="alert" aria-live="assertive"' in INDEX_HTML
    assert "else openRecordStartDialog();" in APP_JS
    assert "if (recordStartDialog.open) return;" in APP_JS
    assert "recordStartDialog.showModal();" in APP_JS


def test_recording_start_never_fails_silently() -> None:
    assert 'btnRecordStartConfirm.textContent = "正在启动…";' in APP_JS
    assert "if (!send({ type: \"recording_start\"" in APP_JS
    assert "后端连接未就绪，无法开始录制" in APP_JS
    assert 'rawError.includes("motor recovery required")' in APP_JS
    assert "机械臂尚未完成安全复位，当前不能录制" in APP_JS
