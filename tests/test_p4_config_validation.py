from __future__ import annotations

import pytest
from pydantic import ValidationError

from lampgo.core.config import load_config


def test_invalid_p4_transport_environment_value_is_rejected(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAMPGO_MOTOR_TRANSPORT", "P4")
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo-home"))

    with pytest.raises(ValidationError, match="motor_transport"):
        load_config(env_file=tmp_path / "missing.env")
