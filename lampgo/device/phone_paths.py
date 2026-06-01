"""Executable discovery helpers for phone control."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def find_adb(configured: str = "") -> str:
    """Resolve adb from an explicit path, PATH, Android SDK env vars, or common local installs."""
    explicit = configured.strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            return str(path)
        found = shutil.which(explicit)
        if found:
            return found
        return ""

    found = shutil.which("adb")
    if found:
        return found

    candidates: list[Path] = []
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(key, "").strip()
        if root:
            candidates.append(Path(root).expanduser() / "platform-tools" / _adb_name())

    candidates.extend(
        [
            Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / _adb_name(),
            Path.home() / "Library" / "Android" / "Sdk" / "platform-tools" / _adb_name(),
            Path("/opt/homebrew/bin") / _adb_name(),
            Path("/usr/local/bin") / _adb_name(),
        ]
    )

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


def _adb_name() -> str:
    return "adb.exe" if os.name == "nt" else "adb"

