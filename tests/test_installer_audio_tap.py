from __future__ import annotations

from types import SimpleNamespace


def test_audio_tap_step_rechecks_after_command_line_tools_install(tmp_path, monkeypatch):
    from lampgo import installer, macos_audio

    lampgo_home = tmp_path / "lampgo"
    lampgo_home.mkdir()
    monkeypatch.setenv("LAMPGO_HOME", str(lampgo_home))
    calls: list[tuple[bool, bool]] = []

    def fake_ensure_macos_audio_tap(*, auto_install_tools=False, build=True):
        calls.append((auto_install_tools, build))
        if len(calls) == 1:
            return SimpleNamespace(
                ok=False,
                status="developer_tools_missing",
                message="需要安装 Apple Command Line Tools 后才能准备系统音频组件。",
                detail="xcrun failed",
                installer_started=False,
                binary_path=None,
            )
        if len(calls) == 2:
            return SimpleNamespace(
                ok=False,
                status="developer_tools_missing",
                message="已打开 Apple Command Line Tools 安装器；请完成安装，完成前音乐律动不可用。",
                detail="xcrun failed",
                installer_started=True,
                binary_path=None,
            )
        binary = lampgo_home / "bin" / "LampgoAudioTap"
        return SimpleNamespace(
            ok=True,
            status="built",
            message="LampGo 系统音频组件已准备完成。",
            detail="",
            installer_started=False,
            binary_path=binary,
        )

    monkeypatch.setattr(macos_audio, "ensure_macos_audio_tap", fake_ensure_macos_audio_tap)
    ctx = installer.InstallContext(
        non_interactive=False,
        assume_yes=True,
        printer=lambda *_args: None,
        input_fn=lambda _prompt: "",
    )

    outcomes = installer._step_audio_tap(ctx)

    assert outcomes[-1].status == "ok"
    assert calls == [(False, True), (True, False), (False, True)]
