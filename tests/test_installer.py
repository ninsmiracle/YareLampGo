"""Tests for the guided installer (``lampgo onboard``)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def fake_lampgo_home(tmp_path, monkeypatch):
    """Isolate ~/.lampgo/ into tmp_path, fresh for each test."""
    lampgo_home = tmp_path / "lampgo"
    lampgo_home.mkdir()
    monkeypatch.setenv("LAMPGO_HOME", str(lampgo_home))
    return {"lampgo": lampgo_home}


@pytest.fixture
def cwd_sandbox(tmp_path, monkeypatch):
    """Put us in a dir with no parent pyproject.toml lookup interference."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


# ---------- half-install scenario -----------------------------------------


def test_run_install_llm_only_works_without_hardware(fake_lampgo_home, cwd_sandbox, monkeypatch):
    """Running only the llm step must not crash and must persist LLM config."""
    from lampgo import installer, personastore

    # Disable the actual ping — ensure httpx never fires.
    monkeypatch.setattr(
        installer,
        "_probe_llm_sync",
        lambda **kwargs: None,
    )

    captured: list[str] = []
    report = installer.run_install(
        non_interactive=True,
        assume_yes=True,
        skip_steps=("env_check", "audio_tap", "hardware", "persona_memory", "codex"),
        llm_provider="mimo",
        llm_key="test-install-key-0000-1234",
        printer=lambda msg="": captured.append(msg),
    )

    # No errors; at least one llm outcome succeeded.
    assert report.errors == [], report.errors
    assert any(o.step == "llm" and o.status == "ok" for o in report.outcomes)

    creds = personastore.get_credentials()
    assert creds.get("llm_api_key") == "test-install-key-0000-1234"
    llm_cfg = personastore.get_overrides_toml().get("llm", {})
    assert llm_cfg.get("provider") == "mimo"


# ---------- incremental (re-run keeps values) -----------------------------


def test_hardware_step_incremental_preserves_existing_motor_port(
    fake_lampgo_home, cwd_sandbox, monkeypatch
):
    """Re-running hardware step with non_interactive must keep current motor_port."""
    from lampgo import installer, personastore

    # Seed an existing override.
    personastore.patch_overrides_toml(
        {"device": {"motor_port": "/dev/ttyUSB_EXISTING", "lamp_id": "AL_KEEP"}}
    )

    # Mock autodetect so we don't poke real hardware.
    import lampgo.autodetect as autodetect

    monkeypatch.setattr(
        autodetect,
        "detect_ports",
        lambda: {
            "motor_port": None,
            "led_port": None,
            "camera_port": None,
            "mic_device": None,
            "all_ports": [],
            "messages": ["mock: no hardware"],
        },
    )

    ctx = installer.InstallContext(
        non_interactive=True,
        assume_yes=True,
        printer=lambda *_a, **_k: None,
    )
    installer._step_hardware(ctx)

    overrides = personastore.get_overrides_toml()
    assert overrides["device"]["motor_port"] == "/dev/ttyUSB_EXISTING"
    assert overrides["device"]["lamp_id"] == "AL_KEEP"


# ---------- persona step --------------------------------------------------


def test_persona_default_creates_missing_files(fake_lampgo_home, monkeypatch):
    from lampgo import installer, personastore

    ctx = installer.InstallContext(
        non_interactive=True,
        assume_yes=True,
        printer=lambda *_a, **_k: None,
    )
    outcomes = installer._step_persona_memory(ctx)
    assert outcomes[-1].status == "ok"
    home = personastore.lampgo_home()
    assert (home / "SOUL.md").exists()
    assert (home / "AGENTS.md").exists()
    assert (home / "PROFILE.md").exists()
    assert (home / "MEMORY.md").exists()


# ---------- skip flag -----------------------------------------------------


def test_run_install_skip_steps_records_skipped_outcomes(fake_lampgo_home, cwd_sandbox):
    from lampgo import installer

    report = installer.run_install(
        non_interactive=True,
        assume_yes=True,
        skip_steps=("audio_tap", "hardware", "llm", "persona_memory", "codex"),
        printer=lambda *_a, **_k: None,
    )
    steps_with_skipped = {
        o.step for o in report.outcomes if o.status == "skipped" and "skipped via --skip" in o.message
    }
    assert steps_with_skipped >= {"audio_tap", "hardware", "llm", "persona_memory", "codex"}
