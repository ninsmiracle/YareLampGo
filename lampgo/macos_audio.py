"""macOS system-audio helper preparation.

The runtime captures system audio through a small ScreenCaptureKit helper.
End users should not need Swift installed at runtime, so we build/cache the
helper during onboarding or the first music-mode launch.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lampgo import personastore

HELPER_NAME = "LampgoAudioTap"


@dataclass(frozen=True)
class AudioTapPrepareResult:
    ok: bool
    status: str
    message: str
    binary_path: Path | None = None
    detail: str = ""
    installer_started: bool = False


def cached_audio_tap_path() -> Path:
    return personastore.lampgo_home() / "bin" / HELPER_NAME


def audio_tap_source_dir() -> Path:
    return Path(__file__).resolve().parent / "macos" / "audio_capture"


def find_audio_tap_binary() -> Path | None:
    env_path = os.environ.get("LAMPGO_AUDIO_TAP_BIN", "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())

    candidates.append(cached_audio_tap_path())
    package_dir = Path(__file__).resolve().parent
    candidates.extend(
        [
            package_dir / "bin" / HELPER_NAME,
            audio_tap_source_dir() / ".build" / "release" / HELPER_NAME,
        ]
    )
    candidates.extend(audio_tap_source_dir().glob(".build/*/release/LampgoAudioTap"))

    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def ensure_macos_audio_tap(*, auto_install_tools: bool = False, build: bool = True) -> AudioTapPrepareResult:
    """Return a ready helper binary, building/caching it if possible.

    Prefer the v1 runtime path: if a helper is not already available, try to
    build it directly with Swift. Some macOS installs report broken ``xcrun
    --sdk`` metadata while ``swift build`` still works, so we only send users
    to the Apple installer after Swift itself is unavailable or the build
    fails with a developer-tools error.
    """

    if platform.system() != "Darwin":
        return AudioTapPrepareResult(
            ok=False,
            status="unsupported_os",
            message="系统音频律动目前只支持 macOS；其他系统请改用麦克风或 synthetic 音源。",
        )

    existing = find_audio_tap_binary()
    if existing is not None:
        return AudioTapPrepareResult(
            ok=True,
            status="ready",
            message="LampGo 系统音频组件已就绪。",
            binary_path=existing,
        )

    source_dir = audio_tap_source_dir()
    if not source_dir.exists():
        return AudioTapPrepareResult(
            ok=False,
            status="source_missing",
            message="LampGo 系统音频组件缺失，请重新安装 LampGo。",
            detail=str(source_dir),
        )

    tools = _check_swift_toolchain()
    if not tools.ok:
        return _developer_tools_missing_result(
            detail=tools.detail,
            auto_install_tools=auto_install_tools,
        )

    if not build:
        return AudioTapPrepareResult(
            ok=False,
            status="not_built",
            message="LampGo 系统音频组件还未构建。",
        )

    built = _build_audio_tap(source_dir)
    if not built.ok or built.binary_path is None:
        if _looks_like_developer_tools_error(built.detail):
            return _developer_tools_missing_result(
                detail=built.detail,
                auto_install_tools=auto_install_tools,
            )
        return built

    cache_path = cached_audio_tap_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built.binary_path, cache_path)
    cache_path.chmod(0o755)
    return AudioTapPrepareResult(
        ok=True,
        status="built",
        message="LampGo 系统音频组件已准备完成。",
        binary_path=cache_path,
    )


@dataclass(frozen=True)
class _ToolCheck:
    ok: bool
    detail: str = ""


def _check_swift_toolchain() -> _ToolCheck:
    if shutil.which("swift") is None:
        return _ToolCheck(False, "未找到 swift 命令")

    cmd = ["swift", "--version"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12, check=False)
    except Exception as exc:  # noqa: BLE001
        return _ToolCheck(False, f"swift 检查失败：{exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return _ToolCheck(False, f"{' '.join(cmd)} 失败：{detail}")
    return _ToolCheck(True, (proc.stdout or proc.stderr or "").strip())


def _developer_tools_missing_result(*, detail: str, auto_install_tools: bool) -> AudioTapPrepareResult:
    installer_started = False
    if auto_install_tools:
        installer_started = _start_command_line_tools_installer()
    message = (
        "需要安装 Apple Command Line Tools 后才能准备系统音频组件。"
        if not installer_started
        else "已打开 Apple Command Line Tools 安装器；请完成安装，完成前音乐律动不可用。"
    )
    return AudioTapPrepareResult(
        ok=False,
        status="developer_tools_missing",
        message=message,
        detail=detail,
        installer_started=installer_started,
    )


def _looks_like_developer_tools_error(detail: str) -> bool:
    text = detail.lower()
    return any(
        marker in text
        for marker in (
            "xcode-select",
            "command line tools",
            "developer tools",
            "unable to lookup item",
            "platformpath",
            "no developer tools",
            "tool 'swift' requires xcode",
        )
    )


def _start_command_line_tools_installer() -> bool:
    try:
        subprocess.Popen(  # noqa: S603
            ["xcode-select", "--install"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _build_audio_tap(source_dir: Path) -> AudioTapPrepareResult:
    cmd = [
        "swift",
        "build",
        "--package-path",
        str(source_dir),
        "-c",
        "release",
        "--product",
        HELPER_NAME,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    except TimeoutError:
        return AudioTapPrepareResult(
            ok=False,
            status="build_timeout",
            message="LampGo 系统音频组件构建超时，请稍后重试。",
        )
    except Exception as exc:  # noqa: BLE001
        return AudioTapPrepareResult(
            ok=False,
            status="build_failed",
            message=f"LampGo 系统音频组件构建失败：{exc}",
        )

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return AudioTapPrepareResult(
            ok=False,
            status="build_failed",
            message="LampGo 系统音频组件构建失败。",
            detail=detail,
        )

    binary = _find_built_audio_tap(source_dir)
    if binary is None:
        return AudioTapPrepareResult(
            ok=False,
            status="build_output_missing",
            message="LampGo 系统音频组件已构建，但没有找到输出文件。",
        )
    return AudioTapPrepareResult(
        ok=True,
        status="built",
        message="LampGo 系统音频组件已构建。",
        binary_path=binary,
    )


def _find_built_audio_tap(source_dir: Path) -> Path | None:
    candidates = [source_dir / ".build" / "release" / HELPER_NAME]
    candidates.extend(source_dir.glob(".build/*/release/LampgoAudioTap"))
    for path in candidates:
        if path.is_file():
            path.chmod(0o755)
            return path
    return None
