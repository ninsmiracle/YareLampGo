from __future__ import annotations

from lampgo.core.config import load_config


def test_load_config_reads_camera_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("LAMPGO_CAMERA_PORT=0\n")
    monkeypatch.delenv("LAMPGO_CAMERA_PORT", raising=False)

    config = load_config(env_file=env_path)

    assert config.camera.port == "0"
