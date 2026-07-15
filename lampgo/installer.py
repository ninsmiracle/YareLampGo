"""Guided first-run installer for lampgo.

Invoked by ``uv run lampgo onboard``. Walks the user through:

1. env_check         — Python / uv / Codex CLI sanity
2. audio_tap         — prepare macOS system-audio helper for music mode
3. hardware          — serial port + camera + microphone selection
4. llm               — provider + api_base + api_key (+ optional ping)
5. persona_memory    — create the local persona and memory files
6. codex             — discover Codex and register LampGo tools automatically
7. summary           — final ``~/.lampgo/`` state

Each step is a standalone function that takes an :class:`InstallContext` and
returns a list of :class:`StepOutcome` entries. The orchestrator supports:

- ``non_interactive=True`` — never prompt, take defaults / flags only
- ``skip_steps=\\{"hardware", ...\\}`` — skip an entire step
- per-step CLI flag passthrough (``motor_port``, ``llm_provider``, ``llm_key``)

The installer is deliberately tolerant: a failure or user-skip in one step does
not block later steps (per "半装容忍：缺啥用啥" in the design plan).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

from lampgo import personastore
from lampgo.agent.codex import ensure_codex_integration, find_codex_binary

StepName = str

ALL_STEPS: tuple[StepName, ...] = (
    "env_check",
    "audio_tap",
    "hardware",
    "llm",
    "persona_memory",
    "codex",
)


# ---------- dataclasses ----------------------------------------------------


@dataclass
class StepOutcome:
    step: StepName
    status: str  # "ok" | "skipped" | "error"
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InstallReport:
    outcomes: list[StepOutcome] = field(default_factory=list)
    performed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, outcome: StepOutcome) -> None:
        self.outcomes.append(outcome)
        bucket = {
            "ok": self.performed,
            "skipped": self.skipped,
            "error": self.errors,
        }.get(outcome.status, self.performed)
        bucket.append(f"[{outcome.step}] {outcome.message}")


@dataclass
class InstallContext:
    non_interactive: bool = False
    assume_yes: bool = False
    skip_steps: frozenset[str] = frozenset()
    motor_port_override: str | None = None
    llm_provider_override: str | None = None
    llm_key_override: str | None = None
    printer: Callable[[str], None] = print
    input_fn: Callable[[str], str] = input

    def out(self, msg: str = "") -> None:
        self.printer(msg)


# ---------- visual helpers -------------------------------------------------

_RULE_WIDTH = 64


def _display_width(s: str) -> int:
    """Approximate terminal cell width counting CJK/wide chars as 2."""
    import unicodedata

    total = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            total += 2
        else:
            total += 1
    return total


def _print_banner(ctx: InstallContext, title: str) -> None:
    """Top-level banner using double-line characters."""
    bar = "═" * _RULE_WIDTH
    ctx.out("")
    ctx.out(bar)
    pad = max(1, (_RULE_WIDTH - _display_width(title)) // 2)
    ctx.out(" " * pad + title)
    ctx.out(bar)


def _print_section(ctx: InstallContext, n: int, total: int, title: str) -> None:
    """Section rule: ``─── 3/6 · 硬件 ───────────────────``."""
    label = f"{n}/{total} · {title}"
    lead = "─── "
    tail_width = max(3, _RULE_WIDTH - _display_width(lead) - _display_width(label) - 1)
    ctx.out("")
    ctx.out(f"{lead}{label} {'─' * tail_width}")


def _print_sub(ctx: InstallContext, title: str) -> None:
    """Sub-section label within a step."""
    ctx.out("")
    ctx.out(f"  ▸ {title}")


def _print_dim(ctx: InstallContext, msg: str) -> None:
    """Indented neutral info line (used for scan results, file paths, etc.)."""
    ctx.out(f"    {msg}")


# ---------- provider presets (mirror of gateway.py) ------------------------

# Kept intentionally in sync with ``gateway.py::_PROVIDER_PRESETS``.
# See the long comment there for why each entry has both ``api_urls``
# (keyed by message_type) and legacy top-level ``base_url`` / ``message_type``.
PROVIDER_PRESETS: dict[str, dict[str, object]] = {
    "mimo": {
        "label": "MiMo",
        "api_urls": {
            "openai": "https://api.xiaomimimo.com/v1",
            "anthropic": "https://api.xiaomimimo.com/anthropic/v1",
        },
        "default_message_type": "openai",
        "default_model": "mimo-v2.5",
        "default_fast_model": "mimo-v2.5",
        "base_url": "https://api.xiaomimimo.com/v1",
        "message_type": "openai",
    },
    "openrouter": {
        "label": "OpenRouter",
        "api_urls": {
            "openai": "https://openrouter.ai/api/v1",
            "anthropic": "https://openrouter.ai/api/v1",
        },
        "default_message_type": "openai",
        "default_model": "anthropic/claude-3.5-sonnet",
        "default_fast_model": "anthropic/claude-3.5-haiku",
        "base_url": "https://openrouter.ai/api/v1",
        "message_type": "openai",
    },
    "anthropic": {
        "label": "Anthropic",
        "api_urls": {
            "anthropic": "https://api.anthropic.com/v1",
        },
        "default_message_type": "anthropic",
        "default_model": "claude-sonnet-4-20250514",
        "default_fast_model": "claude-haiku-4-20250514",
        "base_url": "https://api.anthropic.com/v1",
        "message_type": "anthropic",
    },
    "openai": {
        "label": "OpenAI",
        "api_urls": {
            "openai": "https://api.openai.com/v1",
        },
        "default_message_type": "openai",
        "default_model": "gpt-4o-mini",
        "default_fast_model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "message_type": "openai",
    },
    "deepseek": {
        "label": "DeepSeek",
        "api_urls": {
            "openai": "https://api.deepseek.com/v1",
        },
        "default_message_type": "openai",
        "default_model": "deepseek-chat",
        "default_fast_model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "message_type": "openai",
    },
    "google": {
        "label": "Google Gemini",
        "api_urls": {
            "openai": "https://generativelanguage.googleapis.com/v1beta/openai",
        },
        "default_message_type": "openai",
        "default_model": "gemini-2.5-flash",
        "default_fast_model": "gemini-2.5-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "message_type": "openai",
    },
    "ollama": {
        "label": "Ollama（本地）",
        "api_urls": {
            "openai": "http://127.0.0.1:11434/v1",
        },
        "default_message_type": "openai",
        "default_model": "qwen2.5:7b-instruct",
        "default_fast_model": "qwen2.5:7b-instruct",
        "base_url": "http://127.0.0.1:11434/v1",
        "message_type": "openai",
    },
    "custom": {
        "label": "自定义",
        "api_urls": {},
        "default_message_type": "openai",
        "default_model": "",
        "default_fast_model": "",
        "base_url": "",
        "message_type": "openai",
    },
}


# ---------- ui helpers -----------------------------------------------------


def _ask(
    ctx: InstallContext,
    prompt: str,
    default: str | None = None,
) -> str:
    if ctx.non_interactive:
        return default or ""
    suffix = f" [{default}]" if default else ""
    try:
        raw = ctx.input_fn(f"{prompt}{suffix} > ").strip()
    except EOFError:
        return default or ""
    return raw or (default or "")


def _ask_choice(
    ctx: InstallContext,
    prompt: str,
    choices: list[tuple[str, str]],
    default_idx: int = 0,
) -> str:
    """Numbered-list selector. Returns the value (first element of choice tuple).

    The ``prompt`` leading whitespace is preserved and also applied to the
    option list and the final ``> `` prompt, so callers can nest the selector
    under a sub-section heading simply by passing ``"    选择"``.
    """
    if not choices:
        return ""
    indent = prompt[: len(prompt) - len(prompt.lstrip())]
    if prompt.strip():
        ctx.out(prompt)
    for i, (_value, label) in enumerate(choices):
        marker = "  ← 默认" if i == default_idx else ""
        ctx.out(f"{indent}[{i + 1}] {label}{marker}")
    if ctx.non_interactive:
        return choices[default_idx][0]
    while True:
        raw = _ask(ctx, f"{indent}选择", str(default_idx + 1))
        if not raw:
            return choices[default_idx][0]
        try:
            idx = int(raw) - 1
        except ValueError:
            ctx.out(f"{indent}  输入数字即可。")
            continue
        if 0 <= idx < len(choices):
            return choices[idx][0]
        ctx.out(f"{indent}  不在可选范围。")


def _confirm(ctx: InstallContext, prompt: str, default_yes: bool = True) -> bool:
    if ctx.non_interactive:
        return ctx.assume_yes or default_yes
    default = "Y/n" if default_yes else "y/N"
    try:
        raw = ctx.input_fn(f"{prompt} [{default}] ").strip().lower()
    except EOFError:
        return default_yes
    if not raw:
        return default_yes
    return raw in {"y", "yes", "是", "确认"}


# ---------- Step 1: env check ---------------------------------------------


def _step_env_check(ctx: InstallContext) -> list[StepOutcome]:
    _print_section(ctx, 1, 6, "环境体检")
    outcomes: list[StepOutcome] = []

    def _row(label: str, marker: str, detail: str = "") -> None:
        # 18-cell label column keeps the markers aligned across rows.
        pad = max(1, 18 - _display_width(label))
        ctx.out(f"    {label}{' ' * pad}{marker}  {detail}".rstrip())

    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok_py = sys.version_info >= (3, 12)
    _row("Python", "✓" if ok_py else "✗", f"{py_version}  ({sys.executable})")
    outcomes.append(
        StepOutcome(
            step="env_check",
            status="ok" if ok_py else "error",
            message=f"Python {py_version}" + ("" if ok_py else " —— 需要 >= 3.12"),
        )
    )

    uv = shutil.which("uv")
    if uv:
        _row("uv", "✓", uv)
    else:
        # Note: if uv were missing the user almost certainly couldn't have run
        # `uv run lampgo onboard` to get here, so this mostly catches the
        # `pipx install lampgo` / bare-pip install path.
        _row("uv", "·", "未检测到（推荐安装，管 Python 版本最省心）")
        _print_dim(ctx, "  macOS/Linux  curl -LsSf https://astral.sh/uv/install.sh | sh")
        _print_dim(ctx, "  Homebrew     brew install uv")
        _print_dim(ctx, '  Windows      powershell -c "irm https://astral.sh/uv/install.ps1 | iex"')
    outcomes.append(
        StepOutcome(
            step="env_check",
            status="ok" if uv else "skipped",
            message=f"uv={'installed' if uv else 'missing'}",
        )
    )

    codex = find_codex_binary()
    if codex:
        _row("Codex CLI", "✓", str(codex))
    else:
        _row("Codex CLI", "·", "未检测到（复杂任务暂不可用）")
    outcomes.append(
        StepOutcome(
            step="env_check",
            status="ok" if codex else "skipped",
            message=f"codex={'installed' if codex else 'missing'}",
        )
    )

    home = personastore.lampgo_home()
    _row("lampgo home", " ", str(home))
    return outcomes


# ---------- Step 2: macOS audio helper -------------------------------------


def _step_audio_tap(ctx: InstallContext) -> list[StepOutcome]:
    _print_section(ctx, 2, 6, "系统音频组件")
    from lampgo.macos_audio import ensure_macos_audio_tap

    result = ensure_macos_audio_tap(auto_install_tools=False)
    if result.ok:
        _print_dim(ctx, f"✓ 音乐律动系统音频组件已就绪：{result.binary_path}")
        return [
            StepOutcome(
                step="audio_tap",
                status="ok",
                message=result.message,
                data={"binary_path": str(result.binary_path or "")},
            )
        ]

    if result.status == "unsupported_os":
        _print_dim(ctx, f"· {result.message}")
        return [StepOutcome(step="audio_tap", status="skipped", message=result.message)]

    if result.status == "developer_tools_missing":
        _print_dim(ctx, "音乐律动需要准备一个本机系统音频组件，但当前电脑暂时无法完成构建。")
        if result.detail:
            _print_dim(ctx, result.detail)
        if _confirm(ctx, "    现在打开 Apple 官方安装器？", default_yes=True):
            launched = ensure_macos_audio_tap(auto_install_tools=True, build=False)
            _print_dim(ctx, launched.message)
            if not ctx.non_interactive:
                _print_dim(ctx, "请先完成 Apple 安装器里的安装；安装完成后回到这里按回车继续检查。")
                _print_dim(ctx, "如果没有弹出安装器，或安装失败，请输入 skip，稍后按下方修复命令处理。")
                raw = _ask(ctx, "    安装完成后按回车继续 / 输入 skip 稍后处理", "")
                if raw.strip().lower() not in {"skip", "s", "跳过"}:
                    result = ensure_macos_audio_tap(auto_install_tools=False)
                    if result.ok:
                        _print_dim(ctx, f"✓ 音乐律动系统音频组件已就绪：{result.binary_path}")
                        return [
                            StepOutcome(
                                step="audio_tap",
                                status="ok",
                                message=result.message,
                                data={"binary_path": str(result.binary_path or "")},
                            )
                        ]
            _print_audio_tap_repair_hint(ctx)
            return [
                StepOutcome(
                    step="audio_tap",
                    status="error",
                    message="Apple Command Line Tools 尚未安装完成，音乐律动系统音频暂不可用",
                    data={
                        "installer_started": launched.installer_started,
                        "detail": launched.detail or result.detail,
                    },
                )
            ]

    if result.detail:
        _print_dim(ctx, result.detail)
    if result.status == "developer_tools_missing":
        _print_audio_tap_repair_hint(ctx)
    _print_dim(ctx, f"✗ {result.message}")
    return [
        StepOutcome(
            step="audio_tap",
            status="error",
            message=result.message,
            data={"status": result.status, "detail": result.detail},
        )
    ]


def _print_audio_tap_repair_hint(ctx: InstallContext) -> None:
    _print_dim(ctx, "修复方式：")
    _print_dim(ctx, "  1. 先完成弹出的 Apple Command Line Tools 安装器，安装完成后重跑 `uv run lampgo onboard`。")
    _print_dim(ctx, "  2. 如果没有弹窗，可手动执行：xcode-select --install")
    _print_dim(ctx, "  3. 只有当系统提示已安装但仍构建失败时，再清理后重装：")
    _print_dim(ctx, "     sudo rm -rf /Library/Developer/CommandLineTools")
    _print_dim(ctx, "     xcode-select --install")


# ---------- helpers shared across steps -----------------------------------


def _count_leaves(data: dict[str, Any]) -> int:
    n = 0
    for v in data.values():
        if isinstance(v, dict):
            n += _count_leaves(v)
        else:
            n += 1
    return n


# ---------- Step 3: hardware ----------------------------------------------


def _current_overrides() -> dict[str, Any]:
    return personastore.get_overrides_toml()


def _current_value(section: str, key: str, default: str = "") -> str:
    ov = _current_overrides()
    sec = ov.get(section) if isinstance(ov, dict) else None
    if isinstance(sec, dict) and key in sec and sec[key] not in (None, ""):
        return str(sec[key])
    return default


def _step_hardware(ctx: InstallContext) -> list[StepOutcome]:
    _print_section(ctx, 3, 6, "硬件")
    _print_dim(ctx, "每一项单独询问；macOS 首次探测摄像头/麦克风会弹权限弹窗，授权后重跑。")
    _print_dim(ctx, "扫描中…（首次可能耗时几秒）")

    try:
        from lampgo import autodetect

        detected = autodetect.detect_ports()
    except Exception as exc:
        ctx.out("")
        _print_dim(ctx, f"! 自动探测失败：{exc}")
        detected = {
            "motor_port": None,
            "led_port": None,
            "camera_port": None,
            "mic_device": None,
            "all_ports": [],
            "messages": [f"detect error: {exc}"],
        }

    messages = list(detected.get("messages", []) or [])
    # Carve messages into category buckets so each subsection can render only
    # the scan output that's relevant to it.
    cam_lines: list[str] = []
    mic_lines: list[str] = []  # individual `  Mic N: ...` rows
    mic_header = False
    mic_error: str | None = None
    camera_error: str | None = None
    for msg in messages:
        if msg.startswith("Camera port "):
            cam_lines.append(msg[len("Camera port "):])
        elif msg.startswith("Camera detection skipped") or msg.startswith("No camera detected"):
            camera_error = msg
        elif msg == "Available microphones:":
            mic_header = True
        elif msg.startswith("  Mic "):
            # e.g. "  Mic 2: 外置麦克风 (default)"
            mic_lines.append(msg.lstrip())
        elif msg.startswith("Microphone detection") or msg == "No microphone found.":
            mic_error = msg

    outcomes: list[StepOutcome] = []
    patch: dict[str, Any] = {}

    # ---- motor port ---------------------------------------------------------
    _print_sub(ctx, "电机总线")
    motor_recommended = detected.get("motor_port")
    all_ports = detected.get("all_ports") or []
    if not all_ports:
        _print_dim(ctx, "扫描到 0 个串口。")
    else:
        _print_dim(ctx, f"扫描到 {len(all_ports)} 个串口:")
        for p in all_ports:
            tag = "  (Feetech ✓, 推荐)" if p == motor_recommended else ""
            _print_dim(ctx, f"  · {p}{tag}")

    motor_default = (
        ctx.motor_port_override
        or _current_value("device", "motor_port")
        or (motor_recommended or "")
    )
    motor_choices: list[tuple[str, str]] = []
    for port in all_ports:
        label = port + ("  [推荐]" if port == motor_recommended else "")
        motor_choices.append((port, label))
    motor_choices.append(("__manual__", "手动输入"))
    motor_choices.append(("", "跳过（进入无硬件模式）"))
    default_idx = len(motor_choices) - 1
    if motor_default and motor_default not in [c[0] for c in motor_choices[:-2]]:
        motor_choices.insert(0, (motor_default, f"{motor_default}  (当前)"))
        default_idx = 0
    elif motor_default:
        default_idx = [c[0] for c in motor_choices].index(motor_default)

    motor_choice = _ask_choice(ctx, "    ", motor_choices, default_idx=default_idx)
    if motor_choice == "__manual__":
        motor_choice = _ask(ctx, "    手动输入串口路径", motor_default or "/dev/ttyUSB0")
    motor_port = motor_choice.strip()
    if motor_port:
        patch.setdefault("device", {})["motor_port"] = motor_port
        outcomes.append(StepOutcome(step="hardware", status="ok", message=f"motor_port={motor_port}"))
    else:
        outcomes.append(StepOutcome(step="hardware", status="skipped", message="motor_port unset (no-hw mode)"))

    # ---- led port -----------------------------------------------------------
    _print_sub(ctx, "LED 控制器")
    remaining_ports = [p for p in all_ports if p != motor_port]
    if not remaining_ports:
        _print_dim(ctx, "无剩余串口。")
    else:
        _print_dim(ctx, f"剩余 {len(remaining_ports)} 个串口:")
        for p in remaining_ports:
            tag = "  [推荐]" if p == detected.get("led_port") else ""
            _print_dim(ctx, f"  · {p}{tag}")

    led_default = _current_value("device", "led_port") or (detected.get("led_port") or "")
    led_choices: list[tuple[str, str]] = []
    for port in remaining_ports:
        label = port + ("  [推荐]" if port == detected.get("led_port") else "")
        led_choices.append((port, label))
    led_choices.append(("__manual__", "手动输入"))
    led_choices.append(("", "跳过（不用 LED 表情）"))
    led_default_idx = len(led_choices) - 1
    if led_default:
        for i, (v, _) in enumerate(led_choices):
            if v == led_default:
                led_default_idx = i
                break
    led_choice = _ask_choice(ctx, "    ", led_choices, default_idx=led_default_idx)
    if led_choice == "__manual__":
        led_choice = _ask(ctx, "    手动输入 LED 串口", led_default)
    led_port = led_choice.strip()
    if led_port:
        patch.setdefault("device", {})["led_port"] = led_port
        patch.setdefault("led", {})["port"] = led_port

    # ---- lamp_id ------------------------------------------------------------
    _print_sub(ctx, "Lamp ID")
    _print_dim(ctx, "校准文件查找 key（不用硬件可随意；只影响 assets/calibration/<lamp_id>.json）。")
    lamp_id = _ask(
        ctx,
        "    Lamp ID",
        _current_value("device", "lamp_id", "AL02"),
    )
    if lamp_id:
        patch.setdefault("device", {})["lamp_id"] = lamp_id

    # ---- camera -------------------------------------------------------------
    _print_sub(ctx, "摄像头")
    if cam_lines:
        _print_dim(ctx, f"检测到 {len(cam_lines)} 个可用摄像头:")
        for line in cam_lines:
            _print_dim(ctx, f"  · port {line}")
    elif camera_error:
        _print_dim(ctx, camera_error)
    else:
        _print_dim(ctx, "未检测到可用摄像头。")

    camera_default = _current_value("camera", "port") or str(detected.get("camera_port") or "")
    camera_choices: list[tuple[str, str]] = []
    cam_recommended = detected.get("camera_port")
    if cam_recommended is not None:
        camera_choices.append((str(cam_recommended), f"Camera {cam_recommended}  [推荐]"))
    for idx in ("0", "1", "2", "3"):
        if idx not in [c[0] for c in camera_choices]:
            camera_choices.append((idx, f"Camera {idx}"))
    camera_choices.append(("__manual__", "手动输入"))
    camera_choices.append(("", "跳过（不给 LLM 附图）"))
    cam_default_idx = len(camera_choices) - 1
    if camera_default:
        for i, (v, _) in enumerate(camera_choices):
            if v == camera_default:
                cam_default_idx = i
                break
    cam_choice = _ask_choice(ctx, "    ", camera_choices, default_idx=cam_default_idx)
    if cam_choice == "__manual__":
        cam_choice = _ask(ctx, "    手动输入 camera port", camera_default)
    if cam_choice:
        patch.setdefault("camera", {})["port"] = cam_choice.strip()

    # ---- microphone ---------------------------------------------------------
    _print_sub(ctx, "麦克风")
    if mic_lines:
        _print_dim(ctx, f"检测到 {len(mic_lines)} 个输入设备:")
        for line in mic_lines:
            _print_dim(ctx, f"  · {line}")
    elif mic_error:
        _print_dim(ctx, mic_error)
    elif mic_header:
        _print_dim(ctx, "无可用麦克风。")

    mic_default = _current_value("voice", "mic_device") or str(detected.get("mic_device") or "")
    mic_prompt = _ask(
        ctx,
        "    device index 或名字（回车 = 系统默认）",
        mic_default,
    )
    if mic_prompt:
        patch.setdefault("voice", {})["mic_device"] = mic_prompt.strip()

    if patch:
        personastore.patch_overrides_toml(patch)
        outcomes.append(StepOutcome(step="hardware", status="ok", message=f"wrote {_count_leaves(patch)} hardware field(s)"))
    return outcomes


# ---------- Step 4: LLM ----------------------------------------------------


def _step_llm(ctx: InstallContext) -> list[StepOutcome]:
    _print_section(ctx, 4, 6, "LLM")
    outcomes: list[StepOutcome] = []

    ov = _current_overrides().get("llm") if isinstance(_current_overrides(), dict) else None
    current_llm: dict[str, Any] = ov if isinstance(ov, dict) else {}
    creds = personastore.get_credentials()
    current_key = str(creds.get("llm_api_key") or creds.get("api_key") or "").strip()

    default_provider = (
        ctx.llm_provider_override
        or current_llm.get("provider")
        or "mimo"
    )

    _print_sub(ctx, "Provider")
    provider_choices: list[tuple[str, str]] = [
        (pid, f"{pid}  — {preset['label']}")
        for pid, preset in PROVIDER_PRESETS.items()
    ]
    try:
        default_idx = [c[0] for c in provider_choices].index(str(default_provider))
    except ValueError:
        default_idx = 0
    provider = _ask_choice(ctx, "    ", provider_choices, default_idx=default_idx)

    preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])

    _print_sub(ctx, "Endpoint & 模型")
    api_base_default = current_llm.get("api_base") or preset.get("base_url", "")
    api_base = _ask(ctx, "    API base URL（空 = 使用 provider 预设）", api_base_default)

    model_default = current_llm.get("model") or preset.get("default_model", "")
    fast_model_default = current_llm.get("fast_model") or preset.get("default_fast_model", model_default)
    model = _ask(ctx, "    复杂推理模型 (model)", model_default)
    fast_model = _ask(ctx, "    快速意图模型 (fast_model)", fast_model_default)

    _print_sub(ctx, "API Key")
    _print_dim(ctx, "密钥会写到 ~/.lampgo/credentials.json（权限 0600），不会进 config.toml。")
    key_prompt = "    API key"
    if current_key:
        key_prompt += f"（当前：{personastore.mask_api_key(current_key)}；回车 = 保留）"
    api_key_raw = ctx.llm_key_override
    if api_key_raw is None:
        api_key_raw = _ask(ctx, key_prompt, "")
    effective_key = current_key if not api_key_raw else api_key_raw.strip()

    # Write overrides + credentials
    llm_patch: dict[str, Any] = {
        "provider": provider,
        "api_base": api_base or preset.get("base_url", ""),
        "model": model or model_default,
        "fast_model": fast_model or fast_model_default,
        "message_type": preset.get("message_type", "openai"),
    }
    personastore.patch_overrides_toml({"llm": llm_patch})
    if effective_key:
        personastore.set_credentials({"llm_api_key": effective_key})
    outcomes.append(
        StepOutcome(
            step="llm",
            status="ok",
            message=f"provider={provider} model={llm_patch['model']} key={'set' if effective_key else 'missing'}",
        )
    )

    # Auto-ping (best effort; never blocks)
    if effective_key and llm_patch["model"]:
        _print_sub(ctx, "连通性检查")
        if _confirm(ctx, "    向 provider 发一条 ping？", default_yes=True):
            err = _probe_llm_sync(
                provider=provider,
                api_base=llm_patch["api_base"],
                api_key=effective_key,
                model=llm_patch["fast_model"] or llm_patch["model"],
                message_type=llm_patch["message_type"],
                timeout=10.0,
            )
            if err is None:
                _print_dim(ctx, "✓ ping 成功")
                outcomes.append(StepOutcome(step="llm", status="ok", message="ping ok"))
            else:
                _print_dim(ctx, f"! ping 失败：{err}（配置已保存；可在 Web UI 修复）")
                outcomes.append(StepOutcome(step="llm", status="skipped", message=f"ping failed: {err}"))
    else:
        outcomes.append(StepOutcome(step="llm", status="skipped", message="ping skipped (no key or no model)"))

    return outcomes


def _probe_llm_sync(
    *,
    provider: str,
    api_base: str,
    api_key: str,
    model: str,
    message_type: str,
    timeout: float = 10.0,
) -> str | None:
    """Synchronous LLM ping. Returns None on success or an error string."""
    try:
        import httpx
    except Exception:
        return "httpx 未安装"

    base = (api_base or "").rstrip("/")
    if not base:
        base = (PROVIDER_PRESETS.get(provider, {}) or {}).get("base_url", "")
    if not base:
        return "Base URL 未配置"

    if message_type == "anthropic":
        url = f"{base}/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {"model": model, "max_tokens": 4, "messages": [{"role": "user", "content": "ping"}]}
    else:
        url = f"{base}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 4,
            "temperature": 0,
        }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        return f"连接失败：{exc}"
    if resp.status_code >= 400:
        try:
            data = resp.json()
            err = data.get("error") or data
            msg = (err.get("message") if isinstance(err, dict) else None) or resp.text[:200]
        except Exception:
            msg = resp.text[:200] or f"HTTP {resp.status_code}"
        return f"Provider 返回 {resp.status_code}: {msg}"
    return None


# ---------- Step 5: persona & memory --------------------------------------


def _step_persona_memory(ctx: InstallContext) -> list[StepOutcome]:
    _print_section(ctx, 5, 6, "人设 / 记忆")
    outcomes: list[StepOutcome] = []

    _print_sub(ctx, "来源")
    choices: list[tuple[str, str]] = [
        ("default", "使用默认模板（不覆盖已编辑的 ~/.lampgo/*.md）"),
    ]
    choices.append(("skip", "跳过"))

    choice = _ask_choice(ctx, "    ", choices, default_idx=0)

    if choice == "skip":
        outcomes.append(StepOutcome(step="persona_memory", status="skipped", message="user skipped"))
        return outcomes

    # default — only fill files that don't yet exist
    wrote: list[str] = []
    home = personastore.lampgo_home()
    for name in personastore.PERSONA_FILES:
        p = home / f"{name}.md"
        if not p.exists():
            personastore.write_persona(name, personastore.default_persona(name))
            wrote.append(p.name)
    mem = personastore.memory_core_path()
    if not mem.exists():
        personastore.write_memory_core(personastore.default_memory_core())
        wrote.append(mem.name)
    if wrote:
        _print_dim(ctx, f"写入默认模板：{', '.join(wrote)}")
    else:
        _print_dim(ctx, "所有人设 / 记忆文件已存在，未覆盖。")
    outcomes.append(StepOutcome(step="persona_memory", status="ok", message=f"defaults: {wrote or 'none (kept existing)'}"))
    return outcomes


# ---------- Step 6: Codex --------------------------------------------------


def _step_codex(ctx: InstallContext) -> list[StepOutcome]:
    _print_section(ctx, 6, 6, "Codex 自动接入")
    status = ensure_codex_integration()
    if status.connection == "connected":
        _print_dim(ctx, "Codex 已接通，LampGo 工具已自动注册。")
        outcome_status = "ok"
    elif status.connection in {"not_installed", "login_required"}:
        _print_dim(ctx, status.detail)
        outcome_status = "skipped"
    else:
        _print_dim(ctx, f"Codex 接入失败：{status.detail}")
        outcome_status = "error"
    return [
        StepOutcome(
            step="codex",
            status=outcome_status,
            message=status.detail,
            data=status.to_dict(),
        )
    ]


# ---------- Step 7: summary -----------------------------------------------


def _step_summary(ctx: InstallContext, report: InstallReport) -> None:
    _print_banner(ctx, "lampgo onboard 完成")
    home = personastore.lampgo_home()
    ctx.out(f"  ~/.lampgo/ = {home}")
    config_toml = home / "config.toml"
    creds = home / "credentials.json"
    ctx.out(f"  · config.toml      {'存在' if config_toml.exists() else '未写入'}")
    ctx.out(f"  · credentials.json {'存在 (0600)' if creds.exists() else '未写入'}")

    ctx.out("")
    if report.performed:
        ctx.out("  已完成")
        for line in report.performed:
            ctx.out(f"    ✓ {line}")
    if report.skipped:
        ctx.out("  跳过")
        for line in report.skipped:
            ctx.out(f"    · {line}")
    if report.errors:
        ctx.out("  错误")
        for line in report.errors:
            ctx.out(f"    ✗ {line}")

    ctx.out("")
    ctx.out("  下一步")
    ctx.out("    $ uv run lampgo run --web")
    ctx.out("    浏览器 http://127.0.0.1:8420 —— 其余配置可在 Web UI 设置页修改。")
    ctx.out("")


# ---------- orchestrator ---------------------------------------------------


def run_install(
    *,
    non_interactive: bool = False,
    assume_yes: bool = False,
    skip_steps: Iterable[str] = (),
    motor_port: str | None = None,
    llm_provider: str | None = None,
    llm_key: str | None = None,
    printer: Callable[[str], None] = print,
) -> InstallReport:
    """Run the guided installer.

    Returns an :class:`InstallReport` aggregating per-step outcomes. Never raises
    on user-facing errors — they're recorded in ``report.errors``.
    """
    ctx = InstallContext(
        non_interactive=non_interactive,
        assume_yes=assume_yes,
        skip_steps=frozenset(s.strip() for s in skip_steps if s),
        motor_port_override=motor_port,
        llm_provider_override=llm_provider,
        llm_key_override=llm_key,
        printer=printer,
    )

    _print_banner(ctx, "lampgo · 新手引导")
    if ctx.non_interactive:
        printer("  mode: non-interactive（一路默认 / flag 优先）")
    printer("  随时 Ctrl+C 退出，已完成的步骤会保留。")

    report = InstallReport()
    steps: list[tuple[str, Callable[[InstallContext], list[StepOutcome]]]] = [
        ("env_check", _step_env_check),
        ("audio_tap", _step_audio_tap),
        ("hardware", _step_hardware),
        ("llm", _step_llm),
        ("persona_memory", _step_persona_memory),
        ("codex", _step_codex),
    ]
    for name, fn in steps:
        if name in ctx.skip_steps:
            report.add(StepOutcome(step=name, status="skipped", message="skipped via --skip"))
            continue
        try:
            outcomes = fn(ctx)
        except KeyboardInterrupt:
            printer("")
            printer(f"[abort] 用户在 {name} 步骤中断；已完成的配置已保存。")
            report.add(StepOutcome(step=name, status="skipped", message="user interrupt"))
            break
        except Exception as exc:  # noqa: BLE001
            import traceback

            tb = traceback.format_exc(limit=3)
            report.add(
                StepOutcome(
                    step=name,
                    status="error",
                    message=f"unhandled: {exc}",
                    data={"traceback": tb},
                )
            )
            printer(f"  ✗ {name} 发生未处理错误：{exc}")
            continue
        for o in outcomes:
            report.add(o)

    _step_summary(ctx, report)
    return report


# ---------- small utility: fuzzy parse -------------------------------------


_CHINESE_YES = re.compile(r"^(y|yes|是|确认|ok|好)$", re.IGNORECASE)
_CHINESE_NO = re.compile(r"^(n|no|否|取消|算了)$", re.IGNORECASE)


def parse_yes_no(raw: str, default: bool) -> bool:
    raw = raw.strip()
    if not raw:
        return default
    if _CHINESE_YES.match(raw):
        return True
    if _CHINESE_NO.match(raw):
        return False
    return default


def install_report_to_json(report: InstallReport) -> str:
    return json.dumps(
        {
            "performed": report.performed,
            "skipped": report.skipped,
            "errors": report.errors,
            "outcomes": [o.as_dict() for o in report.outcomes],
        },
        ensure_ascii=False,
        indent=2,
    )
