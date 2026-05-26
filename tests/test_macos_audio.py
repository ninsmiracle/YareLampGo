from __future__ import annotations

import subprocess


def test_audio_tap_uses_explicit_binary(monkeypatch, tmp_path):
    from lampgo import macos_audio

    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(macos_audio.platform, "system", lambda: "Darwin")
    binary = tmp_path / "LampgoAudioTap"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("LAMPGO_AUDIO_TAP_BIN", str(binary))

    result = macos_audio.ensure_macos_audio_tap()

    assert result.ok is True
    assert result.status == "ready"
    assert result.binary_path == binary


def test_audio_tap_uses_bundled_binary_without_building(monkeypatch, tmp_path):
    from lampgo import macos_audio

    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LAMPGO_AUDIO_TAP_BIN", raising=False)
    monkeypatch.setattr(macos_audio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(macos_audio, "audio_tap_source_dir", lambda: tmp_path / "audio_capture")

    bundled = macos_audio.Path(macos_audio.__file__).resolve().parent / "bin" / macos_audio.HELPER_NAME

    result = macos_audio.ensure_macos_audio_tap()

    assert result.ok is True
    assert result.status == "ready"
    assert result.binary_path == bundled


def test_audio_tap_missing_tools_can_start_apple_installer(monkeypatch, tmp_path):
    from lampgo import macos_audio

    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LAMPGO_AUDIO_TAP_BIN", raising=False)
    monkeypatch.setattr(macos_audio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(macos_audio, "find_audio_tap_binary", lambda: None)
    source_dir = tmp_path / "audio_capture"
    source_dir.mkdir()
    monkeypatch.setattr(macos_audio, "audio_tap_source_dir", lambda: source_dir)
    monkeypatch.setattr(macos_audio.shutil, "which", lambda name: None if name == "swift" else f"/usr/bin/{name}")

    launched: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        del kwargs
        launched.append(cmd)

        class FakeProc:
            pass

        return FakeProc()

    monkeypatch.setattr(macos_audio.subprocess, "Popen", fake_popen)

    result = macos_audio.ensure_macos_audio_tap(auto_install_tools=True)

    assert result.ok is False
    assert result.status == "developer_tools_missing"
    assert result.installer_started is True
    assert launched == [["xcode-select", "--install"]]


def test_audio_tap_builds_and_caches_binary(monkeypatch, tmp_path):
    from lampgo import macos_audio

    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LAMPGO_AUDIO_TAP_BIN", raising=False)
    monkeypatch.setattr(macos_audio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(macos_audio, "find_audio_tap_binary", lambda: None)
    monkeypatch.setattr(macos_audio.shutil, "which", lambda name: f"/usr/bin/{name}")
    source_dir = tmp_path / "audio_capture"
    built_dir = source_dir / ".build" / "arm64-apple-macosx" / "release"
    built_dir.mkdir(parents=True)
    monkeypatch.setattr(macos_audio, "audio_tap_source_dir", lambda: source_dir)

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[:2] == ["swift", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="Swift 5.9\n", stderr="")
        assert cmd[:2] == ["swift", "build"]
        binary = built_dir / "LampgoAudioTap"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
        return subprocess.CompletedProcess(cmd, 0, stdout="built\n", stderr="")

    monkeypatch.setattr(macos_audio.subprocess, "run", fake_run)

    result = macos_audio.ensure_macos_audio_tap()

    assert result.ok is True
    assert result.status == "built"
    assert result.binary_path == tmp_path / "home" / "bin" / "LampgoAudioTap"
    assert result.binary_path.read_text(encoding="utf-8") == "#!/bin/sh\n"


def test_audio_tap_build_does_not_require_xcrun_sdk_probe(monkeypatch, tmp_path):
    from lampgo import macos_audio

    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LAMPGO_AUDIO_TAP_BIN", raising=False)
    monkeypatch.setattr(macos_audio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(macos_audio, "find_audio_tap_binary", lambda: None)
    monkeypatch.setattr(macos_audio.shutil, "which", lambda name: f"/usr/bin/{name}")
    source_dir = tmp_path / "audio_capture"
    built_dir = source_dir / ".build" / "release"
    built_dir.mkdir(parents=True)
    monkeypatch.setattr(macos_audio, "audio_tap_source_dir", lambda: source_dir)

    def fake_run(cmd, **kwargs):
        del kwargs
        assert cmd[:2] != ["xcrun", "--sdk"]
        if cmd[:2] == ["swift", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="Swift 6.2\n", stderr="")
        assert cmd[:2] == ["swift", "build"]
        binary = built_dir / "LampgoAudioTap"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
        return subprocess.CompletedProcess(cmd, 0, stdout="built\n", stderr="")

    monkeypatch.setattr(macos_audio.subprocess, "run", fake_run)

    result = macos_audio.ensure_macos_audio_tap()

    assert result.ok is True
    assert result.status == "built"


def test_audio_tap_build_toolchain_error_can_start_installer(monkeypatch, tmp_path):
    from lampgo import macos_audio

    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LAMPGO_AUDIO_TAP_BIN", raising=False)
    monkeypatch.setattr(macos_audio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(macos_audio, "find_audio_tap_binary", lambda: None)
    monkeypatch.setattr(macos_audio.shutil, "which", lambda name: f"/usr/bin/{name}")
    source_dir = tmp_path / "audio_capture"
    source_dir.mkdir()
    monkeypatch.setattr(macos_audio, "audio_tap_source_dir", lambda: source_dir)
    launched: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        if cmd[:2] == ["swift", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="Swift 6.2\n", stderr="")
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="xcrun: error: unable to lookup item 'PlatformPath' from command line tools installation",
        )

    def fake_popen(cmd, **kwargs):
        del kwargs
        launched.append(cmd)

        class FakeProc:
            pass

        return FakeProc()

    monkeypatch.setattr(macos_audio.subprocess, "run", fake_run)
    monkeypatch.setattr(macos_audio.subprocess, "Popen", fake_popen)

    result = macos_audio.ensure_macos_audio_tap(auto_install_tools=True)

    assert result.ok is False
    assert result.status == "developer_tools_missing"
    assert result.installer_started is True
    assert launched == [["xcode-select", "--install"]]
