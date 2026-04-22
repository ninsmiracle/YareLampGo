"""OpenClaw installation detection & one-shot installer.

Exposes two public APIs:

- ``detect_openclaw_integration()``: read-only health check used by both CLI
  and the Web UI health endpoint.
- ``install_openclaw_integration()``: interactive-friendly installer that
  fixes whichever steps are missing, with optional ``auto_confirm``.

The module never imports heavy lampgo deps so it can be safely invoked
before the daemon boots.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------- dataclasses ---------------------------------------------------

@dataclass
class StepStatus:
    ok: bool
    label: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntegrationStatus:
    binary: StepStatus
    config_file: StepStatus
    skill: StepStatus
    plugin: StepStatus
    trusted: StepStatus
    gateway: StepStatus
    plugin_freshness: StepStatus = field(
        default_factory=lambda: StepStatus(ok=True, label="Plugin 同步", detail="")
    )
    plugin_token: StepStatus = field(
        default_factory=lambda: StepStatus(ok=True, label="Plugin Token", detail="")
    )
    overall: str = "unknown"  # "ready" | "degraded" | "partial" | "basic" | "missing" | "error"
    openclaw_home: str = ""
    plugin_source_dir: str = ""
    skill_source_dir: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = {k: (v.as_dict() if isinstance(v, StepStatus) else v) for k, v in asdict(self).items()}
        return data


# ---------- path helpers ---------------------------------------------------

def _openclaw_home() -> Path:
    return Path(os.environ.get("OPENCLAW_HOME") or Path.home() / ".openclaw")


def _openclaw_json(home: Path | None = None) -> Path:
    return (home or _openclaw_home()) / "openclaw.json"


def _extensions_dir(home: Path | None = None) -> Path:
    return (home or _openclaw_home()) / "extensions"


def _lampgo_repo_root() -> Path:
    """Best-effort: locate the lampgo repo root based on this file's position."""
    here = Path(__file__).resolve()
    # lampgo/bridge/openclaw_installer.py -> repo root = parents[2]
    for candidate in (here.parents[2], Path.cwd()):
        if (candidate / "openclaw-plugin-lampgo" / "package.json").exists():
            return candidate
    return here.parents[2]


def plugin_source_dir() -> Path:
    return _lampgo_repo_root() / "openclaw-plugin-lampgo"


def skill_source_dir() -> Path:
    return _lampgo_repo_root() / "openclaw-skills"


# ---------- detection ------------------------------------------------------

def detect_openclaw_integration() -> IntegrationStatus:
    home = _openclaw_home()
    conf_path = _openclaw_json(home)
    ext_dir = _extensions_dir(home)

    binary_path = shutil.which("openclaw")
    binary = StepStatus(
        ok=bool(binary_path),
        label="openclaw CLI",
        detail=binary_path or "未在 PATH 中找到 `openclaw` 命令",
    )

    config_exists = conf_path.exists()
    config_file = StepStatus(
        ok=config_exists,
        label="OpenClaw 配置文件",
        detail=str(conf_path) if config_exists else f"{conf_path} 尚未生成（先运行一次 `openclaw` 初始化）",
    )

    conf_data: dict[str, Any] = {}
    if config_exists:
        try:
            conf_data = json.loads(conf_path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            config_file = StepStatus(
                ok=False,
                label="OpenClaw 配置文件",
                detail=f"读取失败：{exc}",
            )

    # Skill registered
    skill_target = skill_source_dir()
    extra_dirs = _get_in(conf_data, "skills", "load", "extraDirs", default=[])
    skill_registered = any(
        str(Path(d).expanduser().resolve()) == str(skill_target.resolve())
        for d in (extra_dirs or [])
        if isinstance(d, str)
    )
    skill = StepStatus(
        ok=skill_registered,
        label="lampgo AgentSkill",
        detail=(
            f"已在 skills.load.extraDirs 中注册：{skill_target}"
            if skill_registered
            else f"未注册。目标路径：{skill_target}"
        ),
    )

    # Plugin installed (directory present under extensions/)
    plugin_dir = ext_dir / "lampgo"
    plugin_installed = plugin_dir.exists() and (plugin_dir / "package.json").exists()
    plugin = StepStatus(
        ok=plugin_installed,
        label="lampgo Plugin",
        detail=(
            f"已安装：{plugin_dir}"
            if plugin_installed
            else f"未安装。目标路径：{plugin_dir}"
        ),
    )

    # Plugin enabled (OpenClaw's actual schema uses plugins.entries.<id>.enabled)
    entry = _get_in(conf_data, "plugins", "entries", "lampgo", default=None)
    plugin_enabled = isinstance(entry, dict) and entry.get("enabled") is True
    api_base_configured = ""
    if isinstance(entry, dict):
        cfg = entry.get("config")
        if isinstance(cfg, dict):
            val = cfg.get("lampgoApiBase")
            if isinstance(val, str):
                api_base_configured = val
    trusted = StepStatus(
        ok=plugin_enabled,
        label="Plugin 启用",
        detail=(
            f"plugins.entries.lampgo.enabled = true" + (f"（lampgoApiBase = {api_base_configured}）" if api_base_configured else "")
            if plugin_enabled
            else "未启用：plugins.entries.lampgo.enabled 不为 true（OpenClaw 将拒绝加载）"
        ),
    )

    # OpenClaw gateway daemon liveness (cheap TCP probe; no subprocess fork)
    gateway_port = _get_in(conf_data, "gateway", "port", default=18789)
    if not isinstance(gateway_port, int):
        try:
            gateway_port = int(gateway_port)
        except (TypeError, ValueError):
            gateway_port = 18789
    gateway_alive = _probe_tcp("127.0.0.1", gateway_port, timeout=0.5)
    gateway = StepStatus(
        ok=gateway_alive,
        label="OpenClaw gateway",
        detail=(
            f"已在 127.0.0.1:{gateway_port} 响应"
            if gateway_alive
            else (
                f"127.0.0.1:{gateway_port} 无响应。"
                "日常后台常驻：`openclaw gateway start`；"
                "卡死复活：`openclaw gateway restart`；"
                "前台看日志：`openclaw gateway`。"
            )
        ),
    )

    plugin_freshness = _check_plugin_freshness(
        plugin_src=plugin_source_dir(),
        plugin_inst=plugin_dir,
        plugin_installed=plugin_installed,
    )
    plugin_token_status = _check_plugin_token_synced(entry)

    status = IntegrationStatus(
        binary=binary,
        config_file=config_file,
        skill=skill,
        plugin=plugin,
        trusted=trusted,
        gateway=gateway,
        plugin_freshness=plugin_freshness,
        plugin_token=plugin_token_status,
        openclaw_home=str(home),
        plugin_source_dir=str(plugin_target_or_unknown()),
        skill_source_dir=str(skill_target),
    )
    status.overall = _derive_overall(status)
    status.notes = _derive_notes(status)
    return status


_PLUGIN_MTIME_FILES = ("index.ts", "package.json", "openclaw.plugin.json")
_PLUGIN_MTIME_TOLERANCE_SEC = 2.0


def _plugin_dir_mtime(root: Path) -> float:
    """Max mtime among plugin source files (flat layout, skipping node_modules)."""
    if not root.exists():
        return 0.0
    best = 0.0
    for name in _PLUGIN_MTIME_FILES:
        p = root / name
        if not p.exists():
            continue
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > best:
            best = m
    return best


def _check_plugin_freshness(
    *,
    plugin_src: Path,
    plugin_inst: Path,
    plugin_installed: bool,
) -> StepStatus:
    """Compare source vs installed plugin mtimes and warn if source is newer."""
    if not plugin_installed:
        return StepStatus(
            ok=True,
            label="Plugin 同步",
            detail="跳过（插件尚未安装，先装一次即可）",
        )
    if not plugin_src.exists():
        return StepStatus(
            ok=True,
            label="Plugin 同步",
            detail="跳过（未定位到 lampgo 仓库源码）",
        )
    src_mtime = _plugin_dir_mtime(plugin_src)
    inst_mtime = _plugin_dir_mtime(plugin_inst)
    if src_mtime <= 0 or inst_mtime <= 0:
        return StepStatus(
            ok=True,
            label="Plugin 同步",
            detail="跳过（未能读取 mtime）",
        )
    if src_mtime > inst_mtime + _PLUGIN_MTIME_TOLERANCE_SEC:
        delta_sec = int(src_mtime - inst_mtime)
        return StepStatus(
            ok=False,
            label="Plugin 同步",
            detail=(
                f"仓库里 {plugin_src} 的源码比已安装版本新 {delta_sec} 秒，"
                "OpenClaw 当前加载的仍是旧 tool 列表；建议 `lampgo install-openclaw --yes` 重装。"
            ),
        )
    return StepStatus(
        ok=True,
        label="Plugin 同步",
        detail="已安装版本与仓库源码一致",
    )


def _check_plugin_token_synced(plugin_entry: Any) -> StepStatus:
    """Check that ~/.lampgo/credentials.json token matches openclaw.json entry."""
    # Lazy import: personastore pulls in lampgo config chain; keep installer light.
    try:
        from lampgo.personastore import get_plugin_token  # type: ignore
    except Exception as exc:  # pragma: no cover - defensive
        return StepStatus(
            ok=True,
            label="Plugin Token",
            detail=f"跳过（无法导入 personastore: {exc}）",
        )
    local_token = get_plugin_token()
    remote_token = ""
    if isinstance(plugin_entry, dict):
        cfg = plugin_entry.get("config")
        if isinstance(cfg, dict):
            raw = cfg.get("lampgoPluginToken")
            if isinstance(raw, str):
                remote_token = raw.strip()

    if not local_token and not remote_token:
        return StepStatus(
            ok=False,
            label="Plugin Token",
            detail=(
                "~/.lampgo/credentials.json 里还没有 plugin_token；"
                "跑 `lampgo install-openclaw --yes` 会自动生成并写进 openclaw.json，"
                "否则 OpenClaw 调 lampgo_save_memory 会被 401 拒绝。"
            ),
        )
    if local_token and not remote_token:
        return StepStatus(
            ok=False,
            label="Plugin Token",
            detail=(
                "~/.lampgo/credentials.json 里已有 plugin_token，"
                "但 openclaw.json 里没写 plugins.entries.lampgo.config.lampgoPluginToken；"
                "跑 `lampgo install-openclaw --yes` 把它同步过去。"
            ),
        )
    if remote_token and not local_token:
        return StepStatus(
            ok=False,
            label="Plugin Token",
            detail=(
                "openclaw.json 里有 lampgoPluginToken，但 ~/.lampgo/credentials.json 里没了；"
                "跑 `lampgo install-openclaw --yes` 重新生成并同步。"
            ),
        )
    if local_token != remote_token:
        return StepStatus(
            ok=False,
            label="Plugin Token",
            detail=(
                "两侧 token 不一致（lampgo 一侧与 openclaw.json 对不上），"
                "OpenClaw 写记忆会被 401；跑 `lampgo install-openclaw --yes` 重新同步。"
            ),
        )
    return StepStatus(
        ok=True,
        label="Plugin Token",
        detail="lampgo 与 openclaw.json 的 plugin token 已同步",
    )


def _probe_tcp(host: str, port: int, *, timeout: float = 0.5) -> bool:
    """Return True if a TCP connect to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def plugin_target_or_unknown() -> Path:
    src = plugin_source_dir()
    return src if src.exists() else Path("<lampgo 仓库根目录未检测到>")


def _derive_overall(status: IntegrationStatus) -> str:
    if not status.binary.ok:
        return "missing"
    full_config = status.skill.ok and status.plugin.ok and status.trusted.ok
    if full_config and status.gateway.ok:
        return "ready"
    if full_config and not status.gateway.ok:
        return "degraded"
    if any([status.skill.ok, status.plugin.ok]):
        return "partial"
    return "basic"  # CLI 可用，但没装 lampgo 特定的 skill/plugin


def _derive_notes(status: IntegrationStatus) -> list[str]:
    notes: list[str] = []
    if not status.binary.ok:
        notes.append("缺少 `openclaw` 可执行程序：lampgo 将无法 handoff 复杂任务。")
        return notes
    if not status.plugin.ok:
        notes.append("未安装 lampgo plugin：OpenClaw 无法调用机械臂 / LED / 摄像头等硬件 tool。")
    elif not status.trusted.ok:
        notes.append("plugin 已安装但未启用：OpenClaw 启动时会拒绝加载 lampgo 工具。")
    if not status.skill.ok:
        notes.append("未注册 skill：OpenClaw 不会自动识别「跳舞 / 点头 / 看看」等关键词。")
    if not status.gateway.ok:
        notes.append(
            "OpenClaw gateway 未响应：subprocess handoff 仍可工作，但插件 WebSocket、canvas、cron 等能力不可用。"
            "常用命令：`openclaw gateway start`（后台常驻）/ `restart`（卡死复活）/ 裸 `openclaw gateway`（前台调试）。"
        )
    if status.plugin.ok and not status.plugin_freshness.ok:
        notes.append(
            "仓库里的 plugin 源码比已安装版本更新：OpenClaw 仍在跑旧的 tool 列表，"
            "运行 `lampgo install-openclaw --yes` 可以把 `lampgo_get_persona` / `lampgo_get_memory` / `lampgo_save_memory` 等新 tool 带过去。"
        )
    if status.plugin.ok and not status.plugin_token.ok:
        notes.append(
            "Plugin token 未同步：OpenClaw 写入 lampgo 记忆的请求会被 401 拒绝；"
            "运行 `lampgo install-openclaw --yes` 会生成并同步 token。"
        )
    if not notes:
        notes.append("所有组件就绪，可以使用全部 OpenClaw 集成能力。")
    return notes


# ---------- installer ------------------------------------------------------

@dataclass
class InstallReport:
    performed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    final_status: IntegrationStatus | None = None


def install_openclaw_integration(
    *,
    auto_confirm: bool = False,
    check_only: bool = False,
    printer=print,
) -> InstallReport:
    """Install / repair lampgo <-> OpenClaw integration.

    Args:
        auto_confirm: skip interactive prompts (answer yes to everything).
        check_only: only report current state, perform no mutations.
        printer: callable accepting a string; defaults to built-in print.
    """
    report = InstallReport()
    status = detect_openclaw_integration()

    printer("")
    printer("== lampgo ↔ OpenClaw 集成检查 ==")
    for step in (status.binary, status.config_file, status.skill, status.plugin, status.trusted, status.gateway):
        icon = "✓" if step.ok else "✗"
        printer(f"  {icon} {step.label}: {step.detail}")
    printer("")

    if check_only:
        report.final_status = status
        return report

    # Step 1: binary missing -> can't fix here
    if not status.binary.ok:
        report.errors.append("openclaw CLI 未安装，请先按 OpenClaw 官方文档安装 `openclaw` 命令。")
        printer(report.errors[-1])
        report.final_status = status
        return report

    home = _openclaw_home()
    home.mkdir(parents=True, exist_ok=True)
    conf_path = _openclaw_json(home)

    # Step 2: bootstrap openclaw.json if missing
    conf: dict[str, Any] = {}
    if conf_path.exists():
        try:
            conf = json.loads(conf_path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            report.errors.append(f"openclaw.json 读取失败：{exc}")
            printer(report.errors[-1])
            report.final_status = detect_openclaw_integration()
            return report
    else:
        if not _confirm(auto_confirm, f"需要创建 {conf_path} 吗？", printer):
            report.skipped.append("未创建 openclaw.json")
        else:
            conf_path.write_text("{}\n", encoding="utf-8")
            report.performed.append(f"创建 {conf_path}")

    # Step 2b: clean up legacy/invalid `plugins.trusted` BEFORE any `openclaw`
    # subprocess runs, because OpenClaw validates the config on every CLI call
    # and will abort if this key is present.
    legacy_trusted = _get_in(conf, "plugins", "trusted", default=None)
    if legacy_trusted is not None and isinstance(conf.get("plugins"), dict):
        conf["plugins"].pop("trusted", None)
        try:
            conf_path.write_text(json.dumps(conf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            report.performed.append("已移除过时/非法的 plugins.trusted 字段")
        except OSError as exc:
            report.errors.append(f"清理 plugins.trusted 失败：{exc}")

    # Step 3: register AgentSkill
    skill_target = skill_source_dir().resolve()
    if not skill_target.exists():
        report.errors.append(f"未找到 skill 源目录：{skill_target}（是否在 lampgo 仓库根目录运行？）")
    else:
        extra_dirs = _ensure_list(conf, "skills", "load", "extraDirs")
        resolved_existing = [str(Path(d).expanduser().resolve()) for d in extra_dirs if isinstance(d, str)]
        if str(skill_target) in resolved_existing:
            report.skipped.append(f"skill 已在 extraDirs 中：{skill_target}")
        else:
            if _confirm(auto_confirm, f"是否把 {skill_target} 加入 skills.load.extraDirs？", printer):
                extra_dirs.append(str(skill_target))
                report.performed.append(f"skills.load.extraDirs += {skill_target}")
            else:
                report.skipped.append("未注册 skill")

    # Step 4: install plugin via `openclaw plugins install`
    plugin_dir_target = _extensions_dir(home) / "lampgo"
    plugin_src = plugin_source_dir().resolve()
    if not plugin_src.exists():
        report.errors.append(f"未找到 plugin 源目录：{plugin_src}")
    else:
        plugin_installed_already = plugin_dir_target.exists() and (plugin_dir_target / "package.json").exists()
        should_install = True
        if plugin_installed_already:
            if not _confirm(auto_confirm, f"plugin 已存在（{plugin_dir_target}），是否重新安装？", printer, default_no=True):
                should_install = False
                report.skipped.append("plugin 已存在，保持原样")
        if should_install and _confirm(auto_confirm, f"运行 `openclaw plugins install {plugin_src}`？", printer):
            # Break the chicken-and-egg: `openclaw plugins install` validates
            # the existing openclaw.json against the *currently installed*
            # plugin schema. If we've grown a new config field (e.g.
            # lampgoPluginToken) that the old installed schema doesn't know
            # about, validation fails before the new schema is loaded and the
            # install aborts. Solution: blank out our own plugin's config on
            # disk just before installing, then Step 5 below repopulates it.
            if plugin_installed_already:
                try:
                    pre = json.loads(conf_path.read_text(encoding="utf-8")) if conf_path.exists() else {}
                except Exception:
                    pre = {}
                pre_entries = (
                    pre.setdefault("plugins", {}).setdefault("entries", {})
                    if isinstance(pre, dict) else None
                )
                if isinstance(pre_entries, dict) and isinstance(pre_entries.get("lampgo"), dict):
                    pre_entries["lampgo"]["config"] = {}
                    try:
                        conf_path.write_text(
                            json.dumps(pre, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        # Keep our in-memory `conf` in sync so Step 5 rewrites
                        # against a blank config (which is what we want).
                        in_mem = (
                            conf.setdefault("plugins", {}).setdefault("entries", {})
                            if isinstance(conf, dict) else None
                        )
                        if isinstance(in_mem, dict) and isinstance(in_mem.get("lampgo"), dict):
                            in_mem["lampgo"]["config"] = {}
                    except Exception:
                        printer("  ! 尝试清空旧 plugin config 失败，安装可能仍会被旧 schema 拒绝。")
            exit_code, reason = _run_openclaw_install(plugin_src, printer, force=plugin_installed_already)
            if exit_code != 0 and reason == "dangerous_code":
                # The built-in static scanner flagged us (most often a false
                # positive on env/network combinations). Retry with the
                # documented bypass flag — we own this plugin source, so the
                # user is explicitly trusting it.
                if _confirm(
                    auto_confirm,
                    "静态扫描拦截了安装，是否使用 --dangerously-force-unsafe-install 重试？（我们自己维护的 plugin，可信）",
                    printer,
                ):
                    exit_code, reason = _run_openclaw_install(
                        plugin_src, printer, force=True, allow_unsafe=True
                    )
            if exit_code == 0:
                report.performed.append(f"openclaw plugins install {plugin_src}")
            else:
                report.errors.append(f"openclaw plugins install 失败 (exit={exit_code})")

    # Step 5: enable plugin + write pluginConfig (this IS the trust mechanism)
    entries = _ensure_dict(conf, "plugins", "entries")
    lampgo_entry = entries.get("lampgo")
    if not isinstance(lampgo_entry, dict):
        lampgo_entry = {}
        entries["lampgo"] = lampgo_entry

    desired_api_base = _derive_lampgo_api_base()
    try:
        from lampgo.personastore import get_or_create_plugin_token

        desired_plugin_token = get_or_create_plugin_token()
    except Exception:
        desired_plugin_token = ""

    current_cfg = lampgo_entry.get("config") if isinstance(lampgo_entry.get("config"), dict) else {}
    current_api_base = current_cfg.get("lampgoApiBase") if isinstance(current_cfg, dict) else None
    current_token = current_cfg.get("lampgoPluginToken") if isinstance(current_cfg, dict) else None
    needs_enable = lampgo_entry.get("enabled") is not True
    needs_api_base = current_api_base != desired_api_base
    needs_token = desired_plugin_token and current_token != desired_plugin_token

    if needs_enable or needs_api_base or needs_token:
        prompt = "是否启用 lampgo plugin 并写入 lampgoApiBase = " + desired_api_base + "？"
        if _confirm(auto_confirm, prompt, printer):
            lampgo_entry["enabled"] = True
            if not isinstance(lampgo_entry.get("config"), dict):
                lampgo_entry["config"] = {}
            lampgo_entry["config"]["lampgoApiBase"] = desired_api_base
            if desired_plugin_token:
                lampgo_entry["config"]["lampgoPluginToken"] = desired_plugin_token
            report.performed.append(
                f"plugins.entries.lampgo.enabled = true; config.lampgoApiBase = {desired_api_base}"
                + ("; lampgoPluginToken 已更新" if needs_token else "")
            )
        else:
            report.skipped.append("未启用 plugin")
    else:
        report.skipped.append("plugin 已启用且 lampgoApiBase 一致")

    # Step 5c: pin into plugins.allow so OpenClaw doesn't warn about
    # auto-loading untracked local code.
    allow_list = _ensure_list(conf, "plugins", "allow")
    if "lampgo" not in allow_list:
        allow_list.append("lampgo")
        report.performed.append("plugins.allow += lampgo")

    # Write back config
    try:
        conf_path.write_text(json.dumps(conf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        report.errors.append(f"写入 openclaw.json 失败：{exc}")

    report.final_status = detect_openclaw_integration()

    # Summary
    printer("")
    printer("== 安装结果 ==")
    for line in report.performed:
        printer(f"  ✓ {line}")
    for line in report.skipped:
        printer(f"  · {line}")
    for line in report.errors:
        printer(f"  ✗ {line}")
    printer("")
    printer(f"当前集成状态：{report.final_status.overall}")
    for note in report.final_status.notes:
        printer(f"  - {note}")
    return report


# ---------- helpers --------------------------------------------------------

def _confirm(auto: bool, prompt: str, printer, *, default_no: bool = False) -> bool:
    if auto:
        printer(f"[auto] {prompt} -> yes")
        return True
    default = "N/y" if default_no else "Y/n"
    try:
        raw = input(f"{prompt} [{default}] ").strip().lower()
    except EOFError:
        return not default_no
    if not raw:
        return not default_no
    return raw in {"y", "yes", "是", "确认"}


def _get_in(obj: Any, *keys: str, default: Any = None) -> Any:
    cur = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _ensure_list(conf: dict, *keys: str) -> list:
    """Navigate nested dict, creating intermediate dicts, returning the list at the leaf."""
    cur = conf
    for k in keys[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    leaf = keys[-1]
    arr = cur.get(leaf)
    if not isinstance(arr, list):
        arr = []
        cur[leaf] = arr
    return arr


def _ensure_dict(conf: dict, *keys: str) -> dict:
    """Like ``_ensure_list`` but for dict leaves."""
    cur = conf
    for k in keys[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    leaf = keys[-1]
    obj = cur.get(leaf)
    if not isinstance(obj, dict):
        obj = {}
        cur[leaf] = obj
    return obj


def _derive_lampgo_api_base() -> str:
    """Derive the base URL the plugin should call to reach lampgo.

    Reads LAMPGO_API_BASE if set; otherwise composes from LAMPGO_WEB_HOST /
    LAMPGO_WEB_PORT; otherwise defaults to http://127.0.0.1:8420.
    """
    explicit = os.environ.get("LAMPGO_API_BASE", "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("LAMPGO_WEB_HOST", "").strip() or "127.0.0.1"
    port = os.environ.get("LAMPGO_WEB_PORT", "").strip() or "8420"
    # Prefer loopback display for localhost bindings.
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def _run_openclaw_install(
    plugin_src: Path,
    printer,
    *,
    force: bool = False,
    allow_unsafe: bool = False,
) -> tuple[int, str]:
    """Run `openclaw plugins install`.

    Returns ``(exit_code, reason)`` where ``reason`` is one of:
    - ``"ok"`` — install succeeded.
    - ``"dangerous_code"`` — blocked by static scanner (retryable with --dangerously-force-unsafe-install).
    - ``"not_found"`` — openclaw binary missing.
    - ``"other"`` — unclassified failure.
    """
    cmd = ["openclaw", "plugins", "install"]
    if force:
        cmd.append("--force")
    if allow_unsafe:
        cmd.append("--dangerously-force-unsafe-install")
    cmd.append(str(plugin_src))
    printer(f"  $ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        printer("  openclaw 命令找不到")
        return 127, "not_found"
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    for line in (proc.stdout or "").splitlines()[-20:]:
        printer(f"    {line}")
    if proc.returncode != 0:
        for line in (proc.stderr or "").splitlines()[-10:]:
            printer(f"    ! {line}")
    if proc.returncode == 0:
        return 0, "ok"
    if "dangerous code patterns" in combined or "--dangerously-force-unsafe-install" in combined:
        return int(proc.returncode or 1), "dangerous_code"
    return int(proc.returncode or 1), "other"
