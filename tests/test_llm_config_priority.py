"""Regression tests for provider-specific LLM credential precedence."""

from __future__ import annotations

import os

from lampgo import personastore
from lampgo.core.config import load_config_with_provenance


def _clean_lampgo_environment(monkeypatch) -> None:
    for key in list(os.environ):
        if key.startswith("LAMPGO_"):
            monkeypatch.delenv(key, raising=False)


def test_credentials_override_project_env_but_shell_overrides_credentials(monkeypatch, tmp_path) -> None:
    _clean_lampgo_environment(monkeypatch)
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo-home"))
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "LAMPGO_LLM_PROVIDER=deepseek",
                "LAMPGO_LLM_API_BASE=https://api.deepseek.com",
                "LAMPGO_DEEPSEEK_API_KEY=env-deepseek-key",
                "LAMPGO_MIMO_API_KEY=env-mimo-key",
            )
        ),
        encoding="utf-8",
    )
    personastore.set_credentials(
        {
            "llm_primary_provider": "deepseek",
            "deepseek_api_key": "credentials-deepseek-key",
            "mimo_api_key": "credentials-mimo-key",
        }
    )

    config, provenance = load_config_with_provenance(env_file=env_file)

    assert config.llm.api_key == "credentials-deepseek-key"
    assert config.llm.fallback_api_key == "credentials-mimo-key"
    assert provenance["llm.api_key"] == "credentials"
    assert provenance["llm.fallback_api_key"] == "credentials"

    monkeypatch.setenv("LAMPGO_DEEPSEEK_API_KEY", "shell-deepseek-key")
    shell_config, shell_provenance = load_config_with_provenance(env_file=env_file)
    assert shell_config.llm.api_key == "shell-deepseek-key"
    assert shell_provenance["llm.api_key"] == "env"


def test_legacy_mimo_key_is_never_used_as_a_deepseek_key(monkeypatch, tmp_path) -> None:
    _clean_lampgo_environment(monkeypatch)
    monkeypatch.setenv("LAMPGO_HOME", str(tmp_path / "lampgo-home"))
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LAMPGO_LLM_PROVIDER=deepseek\nLAMPGO_LLM_API_BASE=https://api.deepseek.com\n",
        encoding="utf-8",
    )
    # Old installations have a single unlabelled key. It came from the former
    # MiMo-only setup, so migration must retain it only for the MiMo route.
    personastore.set_credentials({"llm_api_key": "legacy-mimo-key"})

    config, provenance = load_config_with_provenance(env_file=env_file)

    assert config.llm.api_key == ""
    assert config.llm.fallback_api_key == "legacy-mimo-key"
    assert provenance["llm.fallback_api_key"] == "credentials"
