#!/usr/bin/env python3
"""Cross-platform dependency installer for a LampGo source checkout.

The POSIX and PowerShell launchers only bootstrap ``uv``.  This module is the
single source of truth for platform checks, system packages, ``uv sync``,
verification, and persistent diagnostics.
"""

from __future__ import annotations

import argparse
import ctypes.util
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PUBLIC_PYPI = "https://pypi.org/simple"
RUNTIME_EXTRAS = ("voice", "perception", "bridge")
PRIVATE_INDEX_MARKERS = ("pkgs.d.xiaomi.net",)


class InstallError(RuntimeError):
    """A user-actionable installation failure."""


@dataclass(frozen=True)
class HostInfo:
    family: str
    system: str
    machine: str
    version: str
    libc: str = ""

    @property
    def label(self) -> str:
        parts = [self.system, self.version, self.machine]
        if self.libc:
            parts.append(self.libc)
        return " / ".join(part for part in parts if part)


def detect_host(
    *,
    system_name: str | None = None,
    machine: str | None = None,
    version: str | None = None,
    libc: str | None = None,
) -> HostInfo:
    system = system_name or platform.system()
    arch = (machine or platform.machine()).lower()
    if system == "Darwin":
        return HostInfo("macos", "macOS", arch, version or platform.mac_ver()[0])
    if system == "Windows":
        return HostInfo("windows", "Windows", arch, version or platform.version())
    if system == "Linux":
        libc_value = libc
        if libc_value is None:
            libc_name, libc_version = platform.libc_ver()
            libc_value = " ".join(part for part in (libc_name, libc_version) if part)
        return HostInfo("linux", "Linux", arch, version or platform.release(), libc_value or "")
    return HostInfo("unsupported", system or "unknown", arch, version or platform.release())


def validate_host(host: HostInfo) -> list[str]:
    """Validate the wheel support matrix and return non-fatal warnings."""

    if host.family == "unsupported":
        raise InstallError(f"不支持的操作系统：{host.label}")

    if host.family == "macos":
        if host.machine not in {"arm64", "aarch64"}:
            raise InstallError(
                "当前锁定依赖和 LampgoAudioTap 只完整支持 Apple Silicon；"
                f"检测到 {host.machine or 'unknown'}。Intel Mac 暂不能保证安装成功。"
            )
        major_text = (host.version or "0").split(".", 1)[0]
        try:
            major = int(major_text)
        except ValueError:
            major = 0
        if major and major < 14:
            raise InstallError(
                f"当前 LiveKit/Silero 锁定依赖要求 macOS 14 或更高版本；检测到 macOS {host.version}。"
            )

    if host.family == "windows" and host.machine not in {"amd64", "x86_64"}:
        raise InstallError(
            "当前 LiveKit 原生 wheel 只覆盖 Windows x64；"
            f"检测到 {host.machine or 'unknown'}。Windows ARM64 暂不支持。"
        )

    if host.family == "linux" and host.machine not in {"amd64", "x86_64", "arm64", "aarch64"}:
        raise InstallError(
            "当前锁定依赖只验证了 Linux x64/ARM64；"
            f"检测到 {host.machine or 'unknown'}。"
        )

    warnings: list[str] = []
    if host.family == "windows":
        warnings.append("Windows 已覆盖依赖安装；LampGo 的 Unix IPC/进程管理仍在适配，暂不承诺端到端运行。")
    if host.family == "linux" and "musl" in host.libc.lower():
        warnings.append("检测到 musl Linux；部分 LiveKit 原生 wheel 仅提供 manylinux/glibc 版本，安装可能失败。")
    return warnings


def build_uv_sync_command(
    uv_binary: str,
    *,
    include_dev: bool,
    dry_run: bool,
    index_url: str,
) -> list[str]:
    command = [uv_binary, "sync", "--locked"]
    for extra in RUNTIME_EXTRAS:
        command.extend(("--extra", extra))
    command.extend(("--group", "dev") if include_dev else ("--no-dev",))
    command.extend(("--default-index", index_url))
    if dry_run:
        command.append("--dry-run")
    return command


_SECRET_VALUE_RE = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|registration[_-]?token|password|secret)\s*[=:]\s*)([^\s,;]+)"
)
_URL_RE = re.compile(r"https?://[^\s'\"]+", re.IGNORECASE)


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return "https://***"
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if port:
        netloc = f"{netloc}:{port}"
    query = "***" if parsed.query else ""
    fragment = "***" if parsed.fragment else ""
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))


def redact(text: str) -> str:
    text = _URL_RE.sub(_redact_url, text)
    return _SECRET_VALUE_RE.sub(r"\1***", text)


def assert_public_dependency_sources(project_root: Path) -> None:
    for relative in ("pyproject.toml", "uv.lock"):
        path = project_root / relative
        content = path.read_text(encoding="utf-8").lower()
        for marker in PRIVATE_INDEX_MARKERS:
            if marker in content:
                raise InstallError(
                    f"{relative} 仍包含小米内网依赖源 {marker}；请更新分支和 uv.lock 后重试。"
                )


def _default_log_path() -> Path:
    override = os.environ.get("LAMPGO_INSTALL_LOG_DIR", "").strip()
    directory = Path(override).expanduser() if override else Path.home() / ".lampgo" / "logs"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return directory / f"install-{stamp}.log"


def _format_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def _console_safe(message: str, encoding: str | None = None) -> str:
    """Render text without failing on legacy Windows console encodings."""

    target_encoding = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    return message.encode(target_encoding, errors="backslashreplace").decode(target_encoding)


class Installer:
    def __init__(self, project_root: Path, log_path: Path) -> None:
        self.project_root = project_root
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("a", encoding="utf-8", buffering=1)
        self.stage = "初始化"

    def close(self) -> None:
        self._log.close()

    def emit(self, message: str = "") -> None:
        safe = redact(message.rstrip("\n"))
        print(_console_safe(safe), flush=True)
        self._log.write(safe + "\n")

    def run(self, command: Sequence[str], *, stage: str) -> None:
        self.stage = stage
        self.emit(f"\n[{stage}] $ {_format_command(command)}")
        started = time.monotonic()
        child_env = os.environ.copy()
        child_env.update({"PYTHONUTF8": "1", "UV_NO_PROGRESS": "1", "NO_COLOR": "1"})
        try:
            process = subprocess.Popen(  # noqa: S603
                list(command),
                cwd=self.project_root,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise InstallError(f"无法启动命令：{command[0]}（{exc}）") from exc

        assert process.stdout is not None
        for line in process.stdout:
            self.emit(line)
        return_code = process.wait()
        elapsed = time.monotonic() - started
        self.emit(f"[{stage}] exit={return_code} elapsed={elapsed:.1f}s")
        if return_code != 0:
            raise InstallError(f"{stage}失败（退出码 {return_code}）")


def _admin_prefix() -> list[str]:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    sudo = shutil.which("sudo")
    if sudo:
        return [sudo]
    raise InstallError(
        "Linux 缺少必要系统包，且当前用户不是 root、系统也没有 sudo。"
        "请先手动安装 PortAudio 和 C 编译工具。"
    )


def install_linux_system_dependencies(installer: Installer, *, dry_run: bool) -> None:
    needs_portaudio = not bool(ctypes.util.find_library("portaudio"))
    needs_build_tools = (shutil.which("cc") is None and shutil.which("gcc") is None) or not Path(
        "/usr/include/linux/input.h"
    ).is_file()
    if not needs_portaudio and not needs_build_tools:
        installer.emit("[系统依赖] PortAudio 与 C 编译工具已就绪。")
        return

    prefix = _admin_prefix()
    if shutil.which("apt-get"):
        packages = []
        if needs_portaudio:
            packages.append("libportaudio2")
        if needs_build_tools:
            packages.extend(("build-essential", "linux-libc-dev"))
        commands = [prefix + ["apt-get", "update"], prefix + ["apt-get", "install", "-y", *packages]]
    elif shutil.which("dnf"):
        packages = (["portaudio"] if needs_portaudio else []) + (
            ["gcc", "glibc-devel", "kernel-headers"] if needs_build_tools else []
        )
        commands = [prefix + ["dnf", "install", "-y", *packages]]
    elif shutil.which("yum"):
        packages = (["portaudio"] if needs_portaudio else []) + (
            ["gcc", "glibc-devel", "kernel-headers"] if needs_build_tools else []
        )
        commands = [prefix + ["yum", "install", "-y", *packages]]
    elif shutil.which("zypper"):
        packages = (["portaudio"] if needs_portaudio else []) + (
            ["gcc", "glibc-devel", "linux-glibc-devel"] if needs_build_tools else []
        )
        commands = [prefix + ["zypper", "--non-interactive", "install", *packages]]
    elif shutil.which("pacman"):
        packages = (["portaudio"] if needs_portaudio else []) + (
            ["base-devel", "linux-api-headers"] if needs_build_tools else []
        )
        commands = [prefix + ["pacman", "-S", "--needed", "--noconfirm", *packages]]
    else:
        raise InstallError(
            "未识别 Linux 包管理器。请先安装 PortAudio，以及用于构建 evdev 的 C 编译器/Linux headers。"
        )

    for command in commands:
        if dry_run:
            installer.emit(f"[系统依赖/dry-run] {_format_command(command)}")
        else:
            installer.run(command, stage="安装 Linux PortAudio")


VOICE_VERIFY_CODE = """
import livekit.agents
import lampgo_livekit_agent
import sounddevice
from livekit import rtc
print("voice imports: ok")
""".strip()

PERCEPTION_VERIFY_CODE = """
import cv2
print("perception import: ok")
""".strip()

BRIDGE_VERIFY_CODE = """
import importlib.util
if importlib.util.find_spec("pyautogui") is None:
    raise SystemExit("pyautogui not found")
print("bridge package: ok")
""".strip()

MAC_AUDIO_VERIFY_CODE = """
from lampgo.macos_audio import ensure_macos_audio_tap
result = ensure_macos_audio_tap(auto_install_tools=False)
print(result.message)
if result.detail:
    print(result.detail)
raise SystemExit(0 if result.ok else 1)
""".strip()


def verify_install(installer: Installer, uv_binary: str, host: HostInfo) -> None:
    prefix = [uv_binary, "run", "--no-sync"]
    installer.run(prefix + ["python", "-c", VOICE_VERIFY_CODE], stage="验证 LiveKit 语音依赖")
    # Keep OpenCV in a separate process.  Importing cv2 and PyAV together on
    # macOS loads duplicate FFmpeg Objective-C classes and produces false alarms.
    installer.run(prefix + ["python", "-c", PERCEPTION_VERIFY_CODE], stage="验证视觉依赖")
    installer.run(prefix + ["python", "-c", BRIDGE_VERIFY_CODE], stage="验证桌面桥接依赖")
    installer.run(prefix + ["lampgo-livekit-agent", "--help"], stage="验证 LiveKit SDK 命令")
    installer.run(prefix + ["lampgo", "--help"], stage="验证 LampGo 命令")
    if host.family == "macos":
        installer.run(prefix + ["python", "-c", MAC_AUDIO_VERIFY_CODE], stage="验证 macOS 音频组件")


def _find_uv() -> str:
    configured = os.environ.get("LAMPGO_UV", "").strip()
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("uv")
    if found:
        return found
    raise InstallError("未找到 uv。请通过 ./install.sh 或 install.ps1 启动安装器。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="安装 LampGo 的全部运行依赖并验证 LiveKit SDK。")
    parser.add_argument("--dev", action="store_true", help="同时安装测试和代码检查依赖。")
    parser.add_argument("--dry-run", action="store_true", help="只让 uv 解析安装计划，不修改 Python 环境。")
    parser.add_argument("--skip-system-deps", action="store_true", help="跳过 Linux 系统包安装。")
    parser.add_argument("--no-verify", action="store_true", help="安装后不执行 import/CLI 验证。")
    parser.add_argument(
        "--index-url",
        default=os.environ.get("LAMPGO_PYPI_INDEX", PUBLIC_PYPI),
        help=f"Python 公网索引（默认：{PUBLIC_PYPI}）。",
    )
    parser.add_argument("--log-file", type=Path, default=None, help="覆盖安装日志路径。")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    log_path = (args.log_file or _default_log_path()).expanduser().resolve()
    installer = Installer(project_root, log_path)
    try:
        host = detect_host()
        installer.emit("LampGo 全量依赖安装器")
        installer.emit(f"项目目录：{project_root}")
        installer.emit(f"运行平台：{host.label}")
        installer.emit(f"日志文件：{log_path}")
        for warning in validate_host(host):
            installer.emit(f"[注意] {warning}")

        assert_public_dependency_sources(project_root)
        installer.emit("[依赖源] pyproject.toml / uv.lock 未发现小米内网源。")
        uv_binary = _find_uv()
        installer.emit(f"[uv] {uv_binary}")

        if host.family == "linux" and not args.skip_system_deps:
            install_linux_system_dependencies(installer, dry_run=args.dry_run)
        elif host.family in {"macos", "windows"}:
            installer.emit("[系统依赖] sounddevice wheel 已自带 PortAudio，无需额外包管理器。")
        else:
            installer.emit("[系统依赖] 已按参数跳过。")

        installer.run(
            build_uv_sync_command(
                uv_binary,
                include_dev=args.dev,
                dry_run=args.dry_run,
                index_url=args.index_url,
            ),
            stage="uv 同步全部 Python 依赖",
        )

        if args.dry_run:
            installer.emit("\n[OK] dry-run 完成；依赖在当前平台可解析。")
        elif args.no_verify:
            installer.emit("\n[OK] 依赖安装完成；已按参数跳过验证。")
        else:
            verify_install(installer, uv_binary, host)
            installer.emit("\n[OK] LampGo 全部依赖安装并验证完成。")
        installer.emit("下一步：uv run lampgo onboard")
        installer.emit(f"安装日志：{log_path}")
        return 0
    except InstallError as exc:
        installer.emit(f"\n[FAIL] 阶段：{installer.stage}")
        installer.emit(f"原因：{exc}")
        installer.emit(f"完整日志：{log_path}")
        installer.emit("修复后可直接重跑同一条安装命令；uv 会复用缓存。")
        return 1
    except Exception as exc:  # noqa: BLE001
        installer.emit(f"\n[FAIL] 未预期错误：{type(exc).__name__}: {exc}")
        installer.emit(f"完整日志：{log_path}")
        return 1
    finally:
        installer.close()


if __name__ == "__main__":
    raise SystemExit(main())
