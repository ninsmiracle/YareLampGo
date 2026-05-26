"""Regression tests for ``_check_plugin_freshness``.

The plugin-sync health check used to warn purely based on mtime, which
produced false positives the instant a user ran ``git stash pop`` /
``git checkout`` / saved a file in the IDE (all of which touch mtimes
without changing bytes).  We now fall back to a content hash when
mtimes diverge — these tests pin that behaviour down so it doesn't
quietly regress.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from lampgo.bridge.openclaw_installer import (
    _check_plugin_freshness,
    _plugin_content_hash,
)

_KEY_FILES = ("index.ts", "package.json", "openclaw.plugin.json")


def _make_plugin(root: Path, contents: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in _KEY_FILES:
        (root / name).write_text(contents.get(name, f"// {name}\n"), encoding="utf-8")


def _touch_mtime(root: Path, when: float) -> None:
    for name in _KEY_FILES:
        p = root / name
        if p.exists():
            os.utime(p, (when, when))


def test_freshness_ok_when_mtime_drifts_but_content_identical(tmp_path: Path) -> None:
    """The exact regression we just fixed: mtime says source is newer by
    thousands of seconds, but the bytes are identical — warning should
    be suppressed with an informative "已忽略" note."""
    src = tmp_path / "src" / "openclaw-plugin-lampgo"
    inst = tmp_path / "installed" / "lampgo"
    same = {
        "index.ts": "export const NAME = 'lampgo';\n",
        "package.json": '{"name":"lampgo"}\n',
        "openclaw.plugin.json": '{"id":"lampgo","version":"1.0.0"}\n',
    }
    _make_plugin(src, same)
    _make_plugin(inst, same)

    now = time.time()
    # Source mtime = now; installed mtime = an hour ago — classic git-stash-pop pattern.
    _touch_mtime(src, now)
    _touch_mtime(inst, now - 3600)

    status = _check_plugin_freshness(plugin_src=src, plugin_inst=inst, plugin_installed=True)
    assert status.ok is True
    assert "已忽略" in status.detail or "内容一致" in status.detail


def test_freshness_warns_when_content_actually_differs(tmp_path: Path) -> None:
    """Real divergence (bytes differ) must still produce a warning."""
    src = tmp_path / "src" / "openclaw-plugin-lampgo"
    inst = tmp_path / "installed" / "lampgo"
    _make_plugin(
        src,
        {
            "index.ts": "export const NAME = 'lampgo-NEW';\n",
            "package.json": '{"name":"lampgo","version":"1.0.1"}\n',
            "openclaw.plugin.json": '{"id":"lampgo","version":"1.0.1"}\n',
        },
    )
    _make_plugin(
        inst,
        {
            "index.ts": "export const NAME = 'lampgo';\n",
            "package.json": '{"name":"lampgo","version":"1.0.0"}\n',
            "openclaw.plugin.json": '{"id":"lampgo","version":"1.0.0"}\n',
        },
    )

    now = time.time()
    _touch_mtime(src, now)
    _touch_mtime(inst, now - 3600)

    status = _check_plugin_freshness(plugin_src=src, plugin_inst=inst, plugin_installed=True)
    assert status.ok is False
    assert "install-openclaw" in status.detail


def test_freshness_ok_when_mtimes_align(tmp_path: Path) -> None:
    """Happy path: mtime doesn't suggest divergence → short-circuit to OK
    without computing any hashes."""
    src = tmp_path / "src" / "openclaw-plugin-lampgo"
    inst = tmp_path / "installed" / "lampgo"
    same = {
        "index.ts": "x\n",
        "package.json": "{}\n",
        "openclaw.plugin.json": "{}\n",
    }
    _make_plugin(src, same)
    _make_plugin(inst, same)

    now = time.time()
    _touch_mtime(src, now - 3600)
    _touch_mtime(inst, now)  # installed is actually newer, still fine

    status = _check_plugin_freshness(plugin_src=src, plugin_inst=inst, plugin_installed=True)
    assert status.ok is True


def test_freshness_skips_when_plugin_not_installed(tmp_path: Path) -> None:
    src = tmp_path / "src" / "openclaw-plugin-lampgo"
    inst = tmp_path / "installed" / "lampgo"
    _make_plugin(src, {})
    status = _check_plugin_freshness(plugin_src=src, plugin_inst=inst, plugin_installed=False)
    assert status.ok is True
    assert "跳过" in status.detail


def test_content_hash_treats_missing_file_distinctly(tmp_path: Path) -> None:
    """Two plugin dirs where one is missing a file must NOT hash the same
    as two dirs where both have empty files of that name."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make_plugin(a, {"index.ts": "x", "package.json": "x", "openclaw.plugin.json": "x"})
    _make_plugin(b, {"index.ts": "x", "package.json": "x", "openclaw.plugin.json": "x"})
    # Remove one file from b to simulate an incomplete install.
    (b / "openclaw.plugin.json").unlink()
    assert _plugin_content_hash(a) != _plugin_content_hash(b)
