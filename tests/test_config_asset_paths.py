from __future__ import annotations

from pathlib import Path

from lampgo.core.config import load_config


def test_default_asset_paths_are_anchored_to_project_root(monkeypatch, tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(project_root / "assets")
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo-home"))
    monkeypatch.delenv("LAMPGO_RECORDINGS_DIR", raising=False)

    config = load_config(env_file=tmp_path / "missing.env")

    assert config.recordings_dir == project_root / "assets" / "recordings"
    assert config.device.calibration_dir == project_root / "assets" / "calibration"


def test_explicit_recordings_dir_is_not_replaced(monkeypatch, tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    custom_dir = tmp_path / "custom-recordings"
    monkeypatch.chdir(project_root / "assets")
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo-home"))

    config = load_config(
        env_file=tmp_path / "missing.env",
        cli_overrides={"recordings_dir": custom_dir},
    )

    assert config.recordings_dir == custom_dir
