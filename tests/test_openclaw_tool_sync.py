"""Regression tests for the OpenClaw plugin tool-sync scanner.

The health UI used to hard-code a tool-name list (``lampgo_get_persona`` /
``lampgo_get_memory`` / ``lampgo_save_memory``) in JavaScript.  Every time
the plugin source gained or lost a tool the hint would drift out of sync.
We now compute ``missing_in_installed`` / ``extra_in_installed`` from the
actual plugin source — these tests pin that scan down so the UI contract
doesn't quietly regress.
"""

from __future__ import annotations

from pathlib import Path

from lampgo.bridge.openclaw_installer import (
    _compute_tool_sync,
    _scan_plugin_tool_names,
)


def _write_plugin(root: Path, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.ts").write_text(body, encoding="utf-8")
    (root / "package.json").write_text("{}\n", encoding="utf-8")


# ---------- _scan_plugin_tool_names ---------------------------------------


def test_scan_picks_up_registertool_names(tmp_path: Path):
    plugin = tmp_path / "plugin"
    _write_plugin(
        plugin,
        """
        api.registerTool({ name: "lampgo_move", label: "a" });
        api.registerTool({
          name: "lampgo_expression",
          label: "b",
        });
        api.registerTool({ name: "lampgo_save_skill", label: "c" });
        """,
    )
    names = _scan_plugin_tool_names(plugin / "index.ts")
    assert names == ["lampgo_move", "lampgo_expression", "lampgo_save_skill"]


def test_scan_ignores_non_lampgo_prefixed_names(tmp_path: Path):
    """A future plugin author might register unrelated tools in the same
    file — the UI diff should still only consider our own tools."""
    plugin = tmp_path / "plugin"
    _write_plugin(
        plugin,
        """
        api.registerTool({ name: "lampgo_move", label: "a" });
        api.registerTool({ name: "third_party_thing", label: "b" });
        """,
    )
    assert _scan_plugin_tool_names(plugin / "index.ts") == ["lampgo_move"]


def test_scan_deduplicates_repeated_registrations(tmp_path: Path):
    """If the same registerTool call appears inside two ``if`` branches we
    still want to show the tool once in the diff, not twice."""
    plugin = tmp_path / "plugin"
    _write_plugin(
        plugin,
        """
        if (flag) {
          api.registerTool({ name: "lampgo_move", label: "a" });
        } else {
          api.registerTool({ name: "lampgo_move", label: "a2" });
        }
        """,
    )
    assert _scan_plugin_tool_names(plugin / "index.ts") == ["lampgo_move"]


def test_scan_returns_empty_when_file_missing(tmp_path: Path):
    assert _scan_plugin_tool_names(tmp_path / "does_not_exist.ts") == []


# ---------- _compute_tool_sync --------------------------------------------


def test_tool_sync_detects_new_source_tools(tmp_path: Path):
    """The real-world shape of this feature: source has 3 new tools the
    installed plugin doesn't know about yet.  This is the "needs-install"
    signal the UI hint renders in full."""
    src = tmp_path / "src"
    inst = tmp_path / "inst"
    _write_plugin(
        src,
        """
        api.registerTool({ name: "lampgo_move" });
        api.registerTool({ name: "lampgo_expression" });
        api.registerTool({ name: "lampgo_save_skill" });
        api.registerTool({ name: "lampgo_delete_skill" });
        api.registerTool({ name: "lampgo_list_skills" });
        """,
    )
    _write_plugin(
        inst,
        """
        api.registerTool({ name: "lampgo_move" });
        api.registerTool({ name: "lampgo_expression" });
        """,
    )
    diff = _compute_tool_sync(plugin_src=src, plugin_inst=inst, plugin_installed=True)
    assert diff["missing_in_installed"] == [
        "lampgo_save_skill",
        "lampgo_delete_skill",
        "lampgo_list_skills",
    ]
    assert diff["extra_in_installed"] == []
    assert diff["installed_tools"] == ["lampgo_move", "lampgo_expression"]


def test_tool_sync_detects_stale_installed_tools(tmp_path: Path):
    src = tmp_path / "src"
    inst = tmp_path / "inst"
    _write_plugin(src, 'api.registerTool({ name: "lampgo_move" });')
    _write_plugin(
        inst,
        """
        api.registerTool({ name: "lampgo_move" });
        api.registerTool({ name: "lampgo_deprecated" });
        """,
    )
    diff = _compute_tool_sync(plugin_src=src, plugin_inst=inst, plugin_installed=True)
    assert diff["missing_in_installed"] == []
    assert diff["extra_in_installed"] == ["lampgo_deprecated"]


def test_tool_sync_when_plugin_not_installed_yet(tmp_path: Path):
    """The UI still wants to *advertise* the source tool list even before
    anything is installed — users should see what they're about to get."""
    src = tmp_path / "src"
    _write_plugin(
        src,
        """
        api.registerTool({ name: "lampgo_move" });
        api.registerTool({ name: "lampgo_expression" });
        """,
    )
    diff = _compute_tool_sync(
        plugin_src=src, plugin_inst=tmp_path / "nowhere", plugin_installed=False
    )
    assert diff["source_tools"] == ["lampgo_move", "lampgo_expression"]
    assert diff["installed_tools"] == []
    assert diff["missing_in_installed"] == []
    assert diff["extra_in_installed"] == []


def test_tool_sync_when_source_missing_returns_empty(tmp_path: Path):
    """No repo? Don't invent a diff — every downstream UI branch checks
    the arrays for emptiness and the hint just won't render this section."""
    diff = _compute_tool_sync(
        plugin_src=tmp_path / "no_src",
        plugin_inst=tmp_path / "no_inst",
        plugin_installed=True,
    )
    assert diff == {
        "source_tools": [],
        "installed_tools": [],
        "missing_in_installed": [],
        "extra_in_installed": [],
    }


def test_derive_notes_uses_live_diff_not_hardcoded_names(tmp_path: Path, monkeypatch):
    """The freshness hint used to hard-code ``lampgo_get_persona`` etc.;
    when we added Level 2 trajectory tools the hint kept pointing at the
    wrong names.  Now the hint must reflect whatever
    ``tool_sync.missing_in_installed`` actually contains for this install."""
    from lampgo.bridge.openclaw_installer import IntegrationStatus, StepStatus, _derive_notes

    status = IntegrationStatus(
        binary=StepStatus(ok=True, label="", detail=""),
        config_file=StepStatus(ok=True, label="", detail=""),
        skill=StepStatus(ok=True, label="", detail=""),
        plugin=StepStatus(ok=True, label="", detail=""),
        trusted=StepStatus(ok=True, label="", detail=""),
        gateway=StepStatus(ok=True, label="", detail=""),
        plugin_freshness=StepStatus(ok=False, label="", detail=""),
        plugin_token=StepStatus(ok=True, label="", detail=""),
        tool_sync={
            "source_tools": [
                "lampgo_move",
                "lampgo_save_skill",
                "lampgo_delete_skill",
            ],
            "installed_tools": ["lampgo_move"],
            "missing_in_installed": ["lampgo_save_skill", "lampgo_delete_skill"],
            "extra_in_installed": [],
        },
    )
    notes = _derive_notes(status)
    joined = "\n".join(notes)
    assert "lampgo_save_skill" in joined
    assert "lampgo_delete_skill" in joined
    # The old hardcoded names must NOT appear unless they're actually in
    # the diff — this was the specific regression we're locking down.
    assert "lampgo_get_persona" not in joined
    assert "lampgo_get_memory" not in joined
    assert "lampgo_save_memory" not in joined


def test_derive_notes_handles_schema_only_freshness(tmp_path: Path):
    """Freshness triggers but tool names unchanged — the note must say
    that explicitly instead of leaving the user to wonder why nothing is
    listed."""
    from lampgo.bridge.openclaw_installer import IntegrationStatus, StepStatus, _derive_notes

    status = IntegrationStatus(
        binary=StepStatus(ok=True, label="", detail=""),
        config_file=StepStatus(ok=True, label="", detail=""),
        skill=StepStatus(ok=True, label="", detail=""),
        plugin=StepStatus(ok=True, label="", detail=""),
        trusted=StepStatus(ok=True, label="", detail=""),
        gateway=StepStatus(ok=True, label="", detail=""),
        plugin_freshness=StepStatus(ok=False, label="", detail=""),
        plugin_token=StepStatus(ok=True, label="", detail=""),
        tool_sync={
            "source_tools": ["lampgo_move"],
            "installed_tools": ["lampgo_move"],
            "missing_in_installed": [],
            "extra_in_installed": [],
        },
    )
    joined = "\n".join(_derive_notes(status))
    assert "schema" in joined or "描述" in joined


def test_unknown_force_option_triggers_retry_without_force(monkeypatch):
    """Newer openclaw CLIs stripped the ``--force`` option; we need to
    transparently retry without it, not report a spurious exit=1 that
    users would otherwise have to debug themselves.  The real-world
    terminal output that motivated this fix literally said
    `error: unknown option '--force'` — pin that exact string, plus a
    couple of alternate phrasings in case openclaw swaps CLI libs."""
    import subprocess

    from lampgo.bridge import openclaw_installer as inst

    calls: list[list[str]] = []

    class _Proc:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, check, capture_output, text):
        calls.append(list(cmd))
        if "--force" in cmd:
            return _Proc(1, "", "error: unknown option '--force'\n")
        return _Proc(0, "[plugins] lampgo: Registered lampgo tools\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    lines: list[str] = []
    rc, reason = inst._run_openclaw_install(
        plugin_src=Path("/tmp/fake-plugin"), printer=lines.append, force=True
    )
    assert rc == 0
    assert reason == "ok"
    # Exactly two subprocess invocations: one with --force, one without.
    assert len(calls) == 2
    assert "--force" in calls[0]
    assert "--force" not in calls[1]
    # The user-facing log must explain why we retried so nobody wonders
    # whether the first `$ openclaw plugins install --force ...` line was
    # a lie.
    joined = "\n".join(lines)
    assert "--force" in joined and "重试" in joined


def test_plugin_exists_error_is_classified(monkeypatch):
    """Newer openclaw CLIs insist the old extension dir must be deleted
    first.  We need a dedicated reason code so the installer can remove the
    directory and retry instead of surfacing a generic exit=1."""
    import subprocess

    from lampgo.bridge import openclaw_installer as inst

    class _Proc:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, check, capture_output, text):
        return _Proc(
            1,
            "",
            "plugin already exists: /Users/me/.openclaw/extensions/lampgo (delete it first)\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc, reason = inst._run_openclaw_install(
        plugin_src=Path("/tmp/fake-plugin"), printer=lambda _line: None, force=False
    )
    assert rc == 1
    assert reason == "plugin_exists"


def test_plugin_exists_matcher_covers_delete_it_first_wording():
    from lampgo.bridge.openclaw_installer import _is_plugin_already_exists_error

    assert _is_plugin_already_exists_error(
        "plugin already exists: /foo/bar (delete it first)"
    )
    assert _is_plugin_already_exists_error(
        "Error: extension already exists somewhere; please delete it first."
    )
    assert not _is_plugin_already_exists_error("unknown option '--force'")


def test_real_repo_plugin_source_exposes_expected_tools():
    """End-to-end sanity check against the real plugin source.  This pins
    the *shape* (a non-empty list including the Level 2 trio) without
    locking in the exact count — the latter would fight every legitimate
    tool addition."""
    from lampgo.bridge.openclaw_installer import plugin_source_dir

    src = plugin_source_dir() / "index.ts"
    names = _scan_plugin_tool_names(src)
    assert "lampgo_save_skill" in names
    assert "lampgo_delete_skill" in names
    assert "lampgo_list_skills" in names
    # Order must mirror the source file top-down, otherwise "missing"
    # hints in the UI read out of order.
    assert names.index("lampgo_save_skill") < names.index("lampgo_delete_skill")
    assert names.index("lampgo_delete_skill") < names.index("lampgo_list_skills")
