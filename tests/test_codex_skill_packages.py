import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _skill_text(name: str) -> str:
    return (REPO_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_codex_skill_packages_have_unique_names_and_routes() -> None:
    setup = _skill_text("lampgo-setup")
    control = _skill_text("lampgo-control")

    assert "\nname: lampgo-setup\n" in setup
    assert "\nname: lampgo-control\n" in control
    assert "use lampgo-setup instead" in control
    assert "lampgo_estop" in control
    assert "references/creator-experiment.md" in control


def test_lampgo_control_agent_metadata_and_reference_exist() -> None:
    skill_dir = REPO_ROOT / "skills" / "lampgo-control"
    metadata = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
    experiment = skill_dir / "references" / "creator-experiment.md"

    assert 'display_name: "LampGo 身体说明书"' in metadata
    assert "$lampgo-control" in metadata
    assert "allow_implicit_invocation: true" in metadata
    assert experiment.is_file()
    assert "no_manual -> body_manual" in experiment.read_text(encoding="utf-8")


def test_codex_skill_installers_include_setup_and_control() -> None:
    shell_installer = (REPO_ROOT / "install-codex-skill.sh").read_text(encoding="utf-8")
    powershell_installer = (REPO_ROOT / "install-codex-skill.ps1").read_text(encoding="utf-8")

    for skill_name in ("lampgo-setup", "lampgo-control"):
        assert skill_name in shell_installer
        assert skill_name in powershell_installer


def test_shell_installer_is_idempotent_for_both_skills(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(tmp_path / "codex")

    if os.name == "nt":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            pytest.skip("PowerShell is not available")
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "install-codex-skill.ps1"),
        ]
    else:
        command = ["bash", str(REPO_ROOT / "install-codex-skill.sh")]

    first = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    second = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    for skill_name in ("lampgo-setup", "lampgo-control"):
        target = tmp_path / "codex" / "skills" / skill_name
        if os.name == "nt":
            assert target.is_dir()
        else:
            assert target.is_symlink()
        assert target.resolve() == (REPO_ROOT / "skills" / skill_name).resolve()
