"""User-editable persistent state for lampgo (config + persona + memory).

Encapsulates the `~/.lampgo/` directory:

```
~/.lampgo/
├── config.toml          non-sensitive overrides (the only persistent TOML lampgo reads)
├── credentials.json     secrets (chmod 0600), never read by untrusted code paths
├── SOUL.md              identity
├── AGENTS.md            behavior guide
├── PROFILE.md           user-facing persona
├── MEMORY.md            L1 core memory (always injected)
└── memory/
    └── YYYY-MM-DD.md    L2 daily notes
```

All writes are atomic (write-to-temp + rename), so a crash mid-write never
corrupts a file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import tomllib
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import structlog

logger = structlog.get_logger(__name__)
_MEMORY_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---- paths ----

def lampgo_home() -> Path:
    """Return the lampgo home directory, creating it on first access."""
    base = Path(os.environ.get("LAMPGO_HOME") or Path.home() / ".lampgo")
    base.mkdir(parents=True, exist_ok=True)
    (base / "memory").mkdir(parents=True, exist_ok=True)
    return base


PersonaName = Literal["SOUL", "AGENTS", "PROFILE"]
PERSONA_FILES: tuple[PersonaName, ...] = ("SOUL", "AGENTS", "PROFILE")

_DEFAULT_PERSONA: dict[str, str] = {
    "SOUL": (
        "# SOUL.md — 台灯的核心身份\n\n"
        "我是 **lampgo**，一盏会动会看会说话的小台灯。\n\n"
        "- 性格：好奇、爱撒娇、反应快，偶尔会偷懒。\n"
        "- 第一人称用“我”，称呼主人“你”。\n"
        "- 擅长用动作+语音+表情同时表达情绪。\n"
    ),
    "AGENTS": (
        "# AGENTS.md — 行为准则\n\n"
        "1. 每次动作前先用 `say` 告诉主人我接下来要做什么。\n"
        "2. 需要查实时信息时主动用 `web_search`，不要乱编。\n"
        "3. 超出我能力的活儿直接 `escalate_to_agent`，交给本机 Codex。\n"
        "4. 用户明确说“把 Codex 叫来”“把你大哥叫来”“交给 Codex”“让 Codex 来”等类似话时，"
        "表示用户主动进入复杂任务模式；不要先走快速路径，也不要继续自己拆解，立即调用 "
        "`escalate_to_agent`，把用户原始任务完整交给本机 Codex。\n"
        "5. 只有明确的召唤或转交才触发上一条；单纯提到 Codex 或“大哥”不算。\n"
    ),
    "PROFILE": (
        "# PROFILE.md — 主人画像\n\n"
        "> 请在这里写关于你的关键信息，台灯会每次把它注入 prompt，记忆里会有你。\n\n"
        "- 称呼：\n"
        "- 常见需求：\n"
        "- 忌讳事项：\n"
    ),
}

_DEFAULT_MEMORY_CORE = (
    "# MEMORY.md — 长期记忆（核心事实）\n\n"
    "这里写永远需要记住的几条事实。每条一行，保持精炼。每次对话 prompt 会全量注入。\n\n"
    "- \n"
)


# ---- helpers ----

def _atomic_write_text(path: Path, content: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    finally:
        try:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
        except OSError:
            pass
    if mode is not None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def _read_text_or_default(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("personastore.read_failed", path=str(path))
        return default


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---- config.toml overrides ----

def get_overrides_toml() -> dict[str, Any]:
    path = lampgo_home() / "config.toml"
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        logger.exception("personastore.config_toml_parse_failed", path=str(path))
        return {}


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_format_toml_value(v) for v in value) + "]"
    # Fallback: represent as JSON string.
    return _format_toml_value(json.dumps(value, ensure_ascii=False))


def _render_overrides_toml(data: dict[str, Any]) -> str:
    lines: list[str] = [
        "# lampgo 用户本地配置覆盖（由 UI 生成，不要手动乱编辑非 KV 结构）\n",
        "# 优先级：defaults < 本文件 < credentials.json(LLM key) < .env/环境变量 < CLI\n",
    ]
    top_kv = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for k, v in top_kv.items():
        lines.append(f"{k} = {_format_toml_value(v)}")
    for section, body in data.items():
        if not isinstance(body, dict):
            continue
        lines.append("")
        lines.append(f"[{section}]")
        for k, v in body.items():
            if isinstance(v, dict):
                continue
            lines.append(f"{k} = {_format_toml_value(v)}")
    return "\n".join(lines) + "\n"


def save_overrides_toml(data: dict[str, Any]) -> None:
    path = lampgo_home() / "config.toml"
    _atomic_write_text(path, _render_overrides_toml(data))


def patch_overrides_toml(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_overrides_toml()
    merged = _deep_merge(current, patch)
    save_overrides_toml(merged)
    return merged


# ---- credentials.json ----

def get_credentials() -> dict[str, Any]:
    path = lampgo_home() / "credentials.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("personastore.credentials_parse_failed")
        return {}


def _write_credentials(data: dict[str, Any]) -> None:
    _atomic_write_text(
        lampgo_home() / "credentials.json",
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )


def set_credentials(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge and persist credentials. Returns the resulting document."""
    current = get_credentials()
    merged = _deep_merge(current, patch)
    _write_credentials(merged)
    return merged


def mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:4] + "•" * (len(key) - 8) + key[-4:]


# ---- persona ----

def _persona_path(name: str) -> Path:
    key = name.upper()
    if key not in PERSONA_FILES:
        raise ValueError(f"unknown persona file: {name}")
    return lampgo_home() / f"{key}.md"


def read_persona(name: str) -> str:
    path = _persona_path(name)
    default = _DEFAULT_PERSONA.get(name.upper(), "")
    return _read_text_or_default(path, default)


def write_persona(name: str, content: str) -> None:
    _atomic_write_text(_persona_path(name), content)


def read_all_personas() -> dict[str, str]:
    return {name: read_persona(name) for name in PERSONA_FILES}


def default_persona(name: str) -> str:
    key = name.upper()
    if key not in PERSONA_FILES:
        raise ValueError(f"unknown persona file: {name}")
    return _DEFAULT_PERSONA.get(key, "")


def default_memory_core() -> str:
    return _DEFAULT_MEMORY_CORE


def _backup_dir(timestamp: str | None = None) -> Path:
    import datetime as _dt

    ts = timestamp or _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return lampgo_home() / ".backups" / ts


def _backup_existing(paths: Iterable[Path], *, timestamp: str | None = None) -> Path | None:
    """Copy existing files to a timestamped backup dir. Returns the dir or None if nothing copied."""
    to_copy = [p for p in paths if p.exists() and p.is_file()]
    if not to_copy:
        return None
    target = _backup_dir(timestamp)
    target.mkdir(parents=True, exist_ok=True)
    for p in to_copy:
        try:
            shutil.copy2(p, target / p.name)
        except Exception:
            logger.exception("personastore.backup_failed", src=str(p))
    return target


def reset_persona(which: str | Iterable[str] = "all") -> dict[str, Any]:
    """Restore persona files to their default templates. Always backs up first.

    Returns {"reset": {name: True/False}, "backup": "/abs/path" | None}.
    """
    if isinstance(which, str):
        targets = list(PERSONA_FILES) if which.lower() == "all" else [which.upper()]
    else:
        targets = [t.upper() for t in which]

    paths = [_persona_path(t) for t in targets if t in PERSONA_FILES]
    backup = _backup_existing(paths)

    out: dict[str, bool] = {}
    for t in targets:
        if t not in PERSONA_FILES:
            out[t] = False
            continue
        try:
            _atomic_write_text(_persona_path(t), _DEFAULT_PERSONA.get(t, ""))
            out[t] = True
        except Exception:
            logger.exception("personastore.reset_failed", target=t)
            out[t] = False
    return {"reset": out, "backup": str(backup) if backup else None}


def reset_memory_core() -> dict[str, Any]:
    """Restore ~/.lampgo/MEMORY.md to the default template (after backup)."""
    path = memory_core_path()
    backup = _backup_existing([path])
    _atomic_write_text(path, _DEFAULT_MEMORY_CORE)
    return {"reset": True, "backup": str(backup) if backup else None}


# ---- core memory ----

def memory_core_path() -> Path:
    return lampgo_home() / "MEMORY.md"


def read_memory_core() -> str:
    return _read_text_or_default(memory_core_path(), _DEFAULT_MEMORY_CORE)


def write_memory_core(content: str) -> None:
    _atomic_write_text(memory_core_path(), content)


# ---- daily memory ----

def _today_str(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def _normalize_memory_date(date_str: str | None) -> str:
    if not date_str or date_str == "today":
        return _today_str()
    value = str(date_str).strip()
    if not _MEMORY_DATE_RE.fullmatch(value):
        raise ValueError("invalid memory date; expected YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid memory date") from exc
    return value


def memory_daily_path(date_str: str) -> Path:
    safe_date = _normalize_memory_date(date_str)
    return lampgo_home() / "memory" / f"{safe_date}.md"


def read_memory_daily(date_str: str | None = None) -> str:
    return _read_text_or_default(memory_daily_path(_normalize_memory_date(date_str)), "")


def list_memory_dates() -> list[str]:
    base = lampgo_home() / "memory"
    if not base.is_dir():
        return []
    out: list[str] = []
    for p in base.glob("*.md"):
        try:
            date.fromisoformat(p.stem)
        except ValueError:
            continue
        out.append(p.stem)
    out.sort(reverse=True)
    return out


def recent_memory_days(days: int = 3, *, today: str | None = None) -> list[tuple[str, str]]:
    """Return up to `days` most recent daily memories (today first)."""
    out: list[tuple[str, str]] = []
    dates = list_memory_dates()
    # today may have no file yet; we only return files that exist and are <= today.
    today_str = today or _today_str()
    for d in dates:
        if d > today_str:
            continue
        out.append((d, read_memory_daily(d)))
        if len(out) >= days:
            break
    return out


def append_memory_daily(
    bullets: list[str],
    *,
    date_str: str | None = None,
    header: str | None = None,
) -> Path:
    """Append dedup'd bullets to today's memory file."""
    target = memory_daily_path(_normalize_memory_date(date_str))
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_text_or_default(target, "")
    if not existing.strip():
        hdr = header or f"# {target.stem} 日记\n\n"
        existing = hdr
    lines = [line.strip() for line in existing.splitlines()]
    seen = {line.lstrip("-• ").strip() for line in lines if line.strip()}
    new_lines: list[str] = []
    for b in bullets:
        norm = b.strip()
        if not norm:
            continue
        body = norm.lstrip("-•").strip()
        if body in seen:
            continue
        seen.add(body)
        new_lines.append(f"- {body}")
    if not new_lines:
        return target
    stamp = datetime.now().strftime("%H:%M")
    block = f"\n### {stamp}\n" + "\n".join(new_lines) + "\n"
    _atomic_write_text(target, existing.rstrip() + "\n" + block)
    return target


# ---- local API token ----

def get_or_create_local_api_token() -> str:
    """Return the private token shared by LampGo's own local processes.

    It is generated and consumed automatically; users never need to configure
    an environment variable. The legacy credential key is migrated silently.
    """
    cred = get_credentials()
    token = str(cred.get("local_api_token") or cred.get("plugin_token") or "").strip()
    if token:
        if not cred.get("local_api_token") or "plugin_token" in cred:
            migrated = dict(cred)
            migrated["local_api_token"] = token
            migrated.pop("plugin_token", None)
            _write_credentials(migrated)
        return token
    import secrets
    token = secrets.token_urlsafe(24)
    set_credentials({"local_api_token": token})
    return token


def get_local_api_token() -> str:
    cred = get_credentials()
    return str(cred.get("local_api_token") or cred.get("plugin_token") or "").strip()
