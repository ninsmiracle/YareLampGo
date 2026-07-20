import sys
import tomllib
from pathlib import Path

import pytest

from tools.install_lampgo import (
    HostInfo,
    Installer,
    InstallError,
    assert_public_dependency_sources,
    build_uv_sync_command,
    detect_host,
    redact,
    validate_host,
)


def test_detects_supported_platform_families() -> None:
    assert detect_host(system_name="Darwin", machine="arm64", version="14.6").family == "macos"
    assert detect_host(system_name="Windows", machine="AMD64", version="11").family == "windows"
    assert detect_host(system_name="Linux", machine="x86_64", version="6.8", libc="glibc 2.39").family == "linux"


def test_rejects_platforms_without_locked_native_wheels() -> None:
    with pytest.raises(InstallError, match="Intel Mac"):
        validate_host(HostInfo("macos", "macOS", "x86_64", "14.0"))
    with pytest.raises(InstallError, match="Windows ARM64"):
        validate_host(HostInfo("windows", "Windows", "arm64", "11"))
    with pytest.raises(InstallError, match="macOS 14"):
        validate_host(HostInfo("macos", "macOS", "arm64", "13.6"))
    with pytest.raises(InstallError, match="Linux x64/ARM64"):
        validate_host(HostInfo("linux", "Linux", "riscv64", "6.8", "glibc 2.39"))


def test_uv_sync_installs_all_runtime_extras_from_lock() -> None:
    command = build_uv_sync_command(
        "uv",
        include_dev=False,
        dry_run=False,
        index_url="https://pypi.org/simple",
    )
    assert command[:3] == ["uv", "sync", "--locked"]
    assert command.count("--extra") == 3
    assert all(extra in command for extra in ("voice", "perception", "bridge"))
    assert "--no-dev" in command
    assert "--default-index" in command


def test_voice_sdk_is_a_default_project_dependency() -> None:
    project_root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = metadata["project"]["dependencies"]
    assert any(item.startswith("lampgo-livekit-agent-sdk") for item in dependencies)
    assert any(item.startswith("livekit") for item in dependencies)
    assert_public_dependency_sources(project_root)


def test_redacts_credentials_from_logs() -> None:
    text = (
        "api_key=abc123 https://alice:password@example.test "
        "https://url-token@example.test/simple?auth=query-token access_token: xyz"
    )
    safe = redact(text)
    assert "abc123" not in safe
    assert "alice" not in safe
    assert "password" not in safe
    assert "url-token" not in safe
    assert "query-token" not in safe
    assert "xyz" not in safe


def test_rejects_private_xiaomi_index_in_lock(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='lampgo'\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text(
        "source = { registry = 'https://pkgs.d.xiaomi.net/simple' }\n",
        encoding="utf-8",
    )
    with pytest.raises(InstallError, match="小米内网"):
        assert_public_dependency_sources(tmp_path)


def test_command_failure_is_persisted_to_log(tmp_path: Path) -> None:
    log_path = tmp_path / "install.log"
    installer = Installer(tmp_path, log_path)
    try:
        with pytest.raises(InstallError, match="退出码 7"):
            installer.run(
                [sys.executable, "-c", "print('diagnostic-line'); raise SystemExit(7)"],
                stage="测试失败日志",
            )
    finally:
        installer.close()

    content = log_path.read_text(encoding="utf-8")
    assert "diagnostic-line" in content
    assert "exit=7" in content
