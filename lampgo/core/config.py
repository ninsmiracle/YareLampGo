"""Configuration models for lampgo.

Config loading priority (highest wins):
  1. CLI arguments
  2. Environment variables (LAMPGO_MOTOR_PORT, etc.)
  3. .env file in project root (optional, advanced override only)
  4. ~/.lampgo/config.toml (written by `lampgo onboard` + Web UI)
  5. Built-in defaults

Note: the legacy ``./lampgo.toml`` in the repo root is no longer read.
Run ``lampgo onboard`` to migrate any values in it to ``~/.lampgo/config.toml``.
``lampgo.toml.example`` is kept purely as field-reference documentation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


class JointLimits(BaseModel):
    """Position limits for a single joint (degrees)."""

    min: float
    max: float


DEFAULT_JOINT_LIMITS: dict[str, JointLimits] = {
    "base_yaw": JointLimits(min=-150.0, max=150.0),
    "base_pitch": JointLimits(min=-100.0, max=65.0),
    "elbow_pitch": JointLimits(min=-90.0, max=100.0),
    "wrist_roll": JointLimits(min=-75.0, max=75.0),
    "wrist_pitch": JointLimits(min=-45.0, max=100.0),
}


class MotorConfig(BaseModel):
    """Per-motor hardware configuration."""

    id: int
    model: str = "sts3215"


DEFAULT_MOTORS: dict[str, MotorConfig] = {
    "base_yaw": MotorConfig(id=1),
    "base_pitch": MotorConfig(id=2),
    "elbow_pitch": MotorConfig(id=3),
    "wrist_roll": MotorConfig(id=4),
    "wrist_pitch": MotorConfig(id=5),
}


class DeviceConfig(BaseModel):
    """Hardware connection settings."""

    motor_port: str = Field(default="", description="Serial port for the Feetech motor bus, e.g. /dev/ttyUSB0")
    led_port: str = Field(default="", description="Serial port for ESP32 LED controller (empty = disabled)")
    lamp_id: str = Field(default="AL02", description="Device identity used for calibration file lookup")
    motors: dict[str, MotorConfig] = Field(default_factory=lambda: dict(DEFAULT_MOTORS))
    use_degrees: bool = Field(default=True, description="Interpret positions as degrees (vs normalised -100..100)")
    disable_torque_on_disconnect: bool = True
    calibration_dir: Path = Field(default=Path("assets/calibration"))


class MotionConfig(BaseModel):
    """Motion runtime tuning parameters."""

    tick_rate_hz: float = Field(default=50.0, gt=0, description="Control loop frequency")
    default_max_velocity: float = Field(
        default=120.0,
        gt=0,
        description="Fallback velocity budget (deg/s) used to cap spring frequency when a "
        "MotionTarget does not specify its own max_velocity. Acceleration is implicit in "
        "the spring-damper dynamics, so there is no parallel default_max_acceleration here "
        "— the hard acceleration cap lives in SafetyConfig.max_acceleration.",
    )
    default_style: str = Field(
        default="gentle",
        description="Biomimetic style preset when MotionTarget.style is unset (gentle|confident|curious|bouncy|hesitant|linear)",
    )
    default_playback_mode: str = Field(
        default="cleaned",
        description="Default playback mode for play_recording skill (raw|cleaned|expressive). "
        "Applied when the web UI or a tool call does not specify one explicitly.",
    )

    # --- Spring-damper playback settings (stream_frames path) ---
    spring_playback_f: float = Field(
        default=5.0,
        gt=0,
        description="Spring natural frequency (Hz) used during stream_frames playback. "
        "Higher = tighter tracking; lower = more elastic feel.",
    )
    spring_playback_z: float = Field(
        default=0.7,
        gt=0,
        description="Spring damping ratio during stream_frames. "
        "0.7 = slight underdamp (micro-elasticity); 1.0 = critical (no overshoot).",
    )

    # --- Idle breathing ---
    breathing_enabled: bool = Field(
        default=True,
        description="Enable slow sinusoidal micro-motion when arm is idle.",
    )
    breathing_amplitude: float = Field(
        default=3.0,
        ge=0,
        description="Peak breathing oscillation amplitude in degrees (before per-joint scaling).",
    )

    # --- Overlapping Action (secondary joint coupling) ---
    overlapping_action: bool = Field(
        default=True,
        description="Enable secondary joints to echo primary joint motion with a delay and reduced amplitude.",
    )

    # --- Anticipation (pre-motion windup) ---
    anticipation_enabled: bool = Field(
        default=True,
        description="Before a large move, briefly move in the opposite direction (windup) for biological readability.",
    )
    anticipation_threshold: float = Field(
        default=10.0,
        gt=0,
        description="Minimum move distance (degrees, max across joints) to trigger anticipation.",
    )
    anticipation_ratio: float = Field(
        default=0.08,
        gt=0,
        description="Windup offset = move_distance * ratio (opposite direction).",
    )
    anticipation_duration_ms: int = Field(
        default=120,
        gt=0,
        description="How long to hold the windup position before starting the main move (ms).",
    )


class SafetyConfig(BaseModel):
    """Safety kernel limits."""

    joint_limits: dict[str, JointLimits] = Field(default_factory=lambda: dict(DEFAULT_JOINT_LIMITS))
    max_velocity: float = Field(default=120.0, gt=0, description="Hard velocity cap (deg/s)")
    max_acceleration: float = Field(default=900.0, gt=0, description="Hard acceleration cap (deg/s^2)")


class LEDConfig(BaseModel):
    """LED controller settings."""

    port: str = Field(default="", description="Serial port for ESP32")
    baud_rate: int = 9600


class LLMConfig(BaseModel):
    """LLM / AI model configuration.

    Used by the intent router (M2) and complex task dispatch.
    All API keys should be set via .env or environment variables, NOT in lampgo.toml.
    """

    PROVIDER_ALIASES: ClassVar[dict[str, str]] = {
        "mimo": "mimo",
        "mi": "mimo",
        "gemini": "google",
    }

    provider: str = Field(default="openai", description="LLM provider: openai, anthropic, gemini, local")

    @classmethod
    def normalize_provider_alias(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        s = v.strip().lower()
        return cls.PROVIDER_ALIASES.get(s, s)

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider_alias(cls, v: Any) -> Any:
        """Normalize legacy / vendor-name aliases to canonical preset keys.

        The Settings UI dropdown is keyed by preset id (mimo / openrouter / ...).
        Older configs and the .env file may use vendor names like "mimo" — map
        them so the runtime value matches what the UI/preset table expects.
        """
        return cls.normalize_provider_alias(v)
    message_type: str = Field(default="openai", description="Message envelope: 'openai' (chat.completions) or 'anthropic' (messages)")
    model: str = Field(default="gpt-4o-mini", description="Model name for complex reasoning tasks")
    fast_model: str = Field(default="gpt-4o-mini", description="Model for simple/fast intent classification")
    api_key: str = Field(default="", description="Set via LAMPGO_LLM_API_KEY env var, NOT in config file")
    api_base: str = Field(default="", description="Custom API base URL (for local/proxy models)")
    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int = Field(
        default=4096,
        gt=0,
        description=(
            "单次对话回复的 token 上限（OpenAI: max_tokens；MiMo: max_completion_tokens）。"
            "对话链若支持多轮工具调用，建议 ≥ 2048。"
        ),
    )
    summary_max_tokens: int = Field(
        default=8192,
        gt=0,
        description=(
            "每日记忆 / 会话摘要任务的输出上限。推理模型（mimo-v2-omni、o-系列、deepseek-r1）"
            "会把预算花在思考链上，至少给 4096 才能稳定产出 bullet。"
        ),
    )
    context_window: int = Field(
        default=128000,
        gt=0,
        description=(
            "模型的输入上下文窗口（tokens）。目前仅作信息记录；将来用于在超限前自动裁剪历史消息。"
            "按所用模型填：mimo-v2-pro/omni ≈ 128k、Claude Sonnet ≈ 200k、gpt-4o-mini ≈ 128k。"
        ),
    )
    timeout_s: float = Field(default=15.0, gt=0, description="HTTP timeout for LLM requests")
    web_search_enabled: bool = Field(default=True, description="Enable MiMo built-in web search when supported")
    web_search_force: bool = Field(default=False, description="Force MiMo web search on every request")
    web_search_limit: int = Field(default=3, ge=1, le=10, description="Max web pages used per MiMo web search")
    web_search_max_keyword: int = Field(default=3, ge=1, le=10, description="Max keywords per MiMo web search")
    web_search_country: str = Field(default="", description="Approximate country for MiMo web search")
    web_search_region: str = Field(default="", description="Approximate region for MiMo web search")
    web_search_city: str = Field(default="", description="Approximate city for MiMo web search")
    max_agent_turns: int = Field(default=20, ge=1, le=50, description="Max LLM agent loop turns")
    max_agent_tool_calls: int = Field(default=50, ge=1, le=100, description="Max total tool calls per agent loop")


class CameraConfig(BaseModel):
    """Camera capture settings used for LLM vision input."""

    port: str = Field(default="", description="Camera device index or path (empty = disabled)")


class VoiceConfig(BaseModel):
    """Voice / TTS / STT configuration."""

    stt_provider: str = Field(default="omni", description="STT provider: omni (mimo-v2-omni), whisper")
    stt_model: str = Field(default="mimo-v2-omni", description="STT model name (for omni provider)")
    tts_provider: str = Field(default="mimo", description="TTS provider: mimo (mimo-v2-tts), edge-tts")
    tts_voice: str = Field(default="mimo_default", description="TTS voice identifier")
    tts_style_prompt: str = Field(default="", description="MiMo TTS style instruction (e.g. '温柔甜美的女声')")
    chat_model: str = Field(default="mimo-v2-pro", description="LLM model for voice chat streaming responses")
    mic_device: str = Field(default="", description="Microphone device index or name (empty = system default)")
    wake_word: str = Field(default="", description="Wake word for hands-free activation (empty = disabled)")
    vad_enabled: bool = Field(default=False, description="Enable voice activity detection")


class WebConfig(BaseModel):
    """Web UI / gateway settings."""

    host: str = Field(default="0.0.0.0", description="Web server bind address")
    port: int = Field(default=8420, ge=1, le=65535, description="Web server port")
    status_interval: float = Field(default=2.0, gt=0, description="Seconds between status broadcasts to WS clients")


class LampgoConfig(BaseModel):
    """Root configuration combining all sub-configs."""

    device: DeviceConfig = Field(default_factory=DeviceConfig)
    motion: MotionConfig = Field(default_factory=MotionConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    led: LEDConfig = Field(default_factory=LEDConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    camera: CameraConfig = Field(default_factory=CameraConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    recordings_dir: Path = Field(default=Path("assets/recordings"))
    socket_path: str = Field(default="/tmp/lampgo.sock", description="Unix socket path for IPC")
    voice_enabled: bool = Field(default=False, description="Enable voice loop on startup")
    web_enabled: bool = Field(default=False, description="Enable web UI on startup")
    home_on_start: bool = Field(default=False, description="Slowly return to safe position on startup")
    no_hw: bool = Field(default=False, description="Skip hardware connections (motors/LED)")
    share_openclaw_memory: bool = Field(
        default=True,
        description="When true, inject OpenClaw MEMORY.md + recent daily notes into lampgo prompts.",
    )


def _find_project_root() -> Path:
    """Walk upward from CWD to find the directory containing pyproject.toml."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return current


def load_config(
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
    cli_overrides: dict | None = None,
) -> LampgoConfig:
    """Load configuration with the priority chain: CLI > env > .env > user toml > defaults.

    Args:
        config_path: Deprecated. Accepted for backwards compatibility but ignored;
            the repo-local ``./lampgo.toml`` is no longer read. Use
            ``~/.lampgo/config.toml`` (written by ``lampgo onboard`` and the Web UI) instead.
        env_file: Explicit path to .env. If None, searches project root.
        cli_overrides: Dict of flat overrides from CLI args (e.g. {"device.motor_port": "/dev/ttyUSB0"}).
    """
    config, _ = load_config_with_provenance(
        config_path=config_path, env_file=env_file, cli_overrides=cli_overrides
    )
    return config


def load_config_with_provenance(
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
    cli_overrides: dict | None = None,
) -> tuple[LampgoConfig, dict[str, str]]:
    """Like :func:`load_config` but also returns a ``{dotted_path: source}`` map.

    Sources are one of ``"default"``, ``"user_config"``, ``"credentials"``,
    ``"env"``, ``"cli"`` — matching the order config layers are applied. This is
    what the Web UI uses to mark fields as "overridden by .env" so we can grey
    them out and hint the user to remove the env var before editing.
    """
    project_root = _find_project_root()

    # 1. Load .env file (populates os.environ for secrets)
    if env_file is None:
        env_file = project_root / ".env"
    if Path(env_file).exists():
        load_dotenv(env_file)

    # 2. Start from built-in defaults — every field begins as "default".
    config = LampgoConfig()
    provenance: dict[str, str] = {}
    for dotted in _enumerate_config_paths(config):
        provenance[dotted] = "default"

    # 3. Merge user overrides from ~/.lampgo/config.toml and credentials.json.
    user_overrides: dict = {}
    try:
        from lampgo.personastore import get_credentials, get_overrides_toml

        user_overrides = get_overrides_toml() or {}
        if user_overrides:
            merged = _deep_merge_dict(config.model_dump(), user_overrides)
            config = LampgoConfig(**merged)
            for dotted in _flatten_dict_keys(user_overrides):
                if dotted in provenance:
                    provenance[dotted] = "user_config"
        creds = get_credentials()
        if creds:
            llm_key = str(creds.get("llm_api_key") or creds.get("api_key") or "").strip()
            if llm_key:
                config.llm.api_key = llm_key
                provenance["llm.api_key"] = "credentials"
    except Exception:
        # Never let a bad user file block startup.
        pass

    # 4. Apply environment variable overrides
    env_fields = _apply_env_overrides(config, track=True)
    for dotted in env_fields:
        provenance[dotted] = "env"

    # 5. Apply CLI overrides
    if cli_overrides:
        cli_fields = _apply_cli_overrides(config, cli_overrides, track=True)
        for dotted in cli_fields:
            provenance[dotted] = "cli"

    return config, provenance


def _enumerate_config_paths(model: LampgoConfig) -> list[str]:
    """Return dotted keys for all scalar leaves of the config (for provenance init)."""
    paths: list[str] = []
    data = model.model_dump()
    for key, value in data.items():
        if isinstance(value, dict):
            for sub in value.keys():
                paths.append(f"{key}.{sub}")
        else:
            paths.append(key)
    return paths


def _flatten_dict_keys(data: dict, prefix: str = "") -> list[str]:
    out: list[str] = []
    for k, v in data.items():
        dotted = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.extend(_flatten_dict_keys(v, dotted))
        else:
            out.append(dotted)
    return out


def _apply_env_overrides(config: LampgoConfig, *, track: bool = False) -> list[str]:
    """Override config fields from LAMPGO_* environment variables.

    When ``track=True`` returns a list of dotted paths that were actually
    overridden; otherwise returns an empty list.
    """
    changed: list[str] = []
    env_map = {
        "LAMPGO_MOTOR_PORT": ("device", "motor_port"),
        "LAMPGO_LED_PORT": ("device", "led_port"),
        "LAMPGO_LAMP_ID": ("device", "lamp_id"),
        "LAMPGO_MOTION_DEFAULT_MAX_VELOCITY": ("motion", "default_max_velocity"),
        "LAMPGO_MOTION_DEFAULT_STYLE": ("motion", "default_style"),
        "LAMPGO_MOTION_DEFAULT_PLAYBACK_MODE": ("motion", "default_playback_mode"),
        "LAMPGO_SAFETY_MAX_VELOCITY": ("safety", "max_velocity"),
        "LAMPGO_LLM_API_KEY": ("llm", "api_key"),
        "LAMPGO_LLM_API_BASE": ("llm", "api_base"),
        "LAMPGO_LLM_PROVIDER": ("llm", "provider"),
        "LAMPGO_LLM_MODEL": ("llm", "model"),
        "LAMPGO_LLM_FAST_MODEL": ("llm", "fast_model"),
        "LAMPGO_LLM_TIMEOUT_S": ("llm", "timeout_s"),
        "LAMPGO_LLM_WEB_SEARCH_ENABLED": ("llm", "web_search_enabled"),
        "LAMPGO_LLM_WEB_SEARCH_FORCE": ("llm", "web_search_force"),
        "LAMPGO_LLM_WEB_SEARCH_LIMIT": ("llm", "web_search_limit"),
        "LAMPGO_LLM_WEB_SEARCH_MAX_KEYWORD": ("llm", "web_search_max_keyword"),
        "LAMPGO_LLM_WEB_SEARCH_COUNTRY": ("llm", "web_search_country"),
        "LAMPGO_LLM_WEB_SEARCH_REGION": ("llm", "web_search_region"),
        "LAMPGO_LLM_WEB_SEARCH_CITY": ("llm", "web_search_city"),
        "LAMPGO_LLM_MAX_AGENT_TURNS": ("llm", "max_agent_turns"),
        "LAMPGO_LLM_MAX_AGENT_TOOL_CALLS": ("llm", "max_agent_tool_calls"),
        "LAMPGO_CAMERA_PORT": ("camera", "port"),
        "LAMPGO_VOICE_STT_PROVIDER": ("voice", "stt_provider"),
        "LAMPGO_VOICE_STT_MODEL": ("voice", "stt_model"),
        "LAMPGO_VOICE_TTS_PROVIDER": ("voice", "tts_provider"),
        "LAMPGO_VOICE_TTS_VOICE": ("voice", "tts_voice"),
        "LAMPGO_VOICE_TTS_STYLE_PROMPT": ("voice", "tts_style_prompt"),
        "LAMPGO_VOICE_CHAT_MODEL": ("voice", "chat_model"),
        "LAMPGO_VOICE_MIC_DEVICE": ("voice", "mic_device"),
        "LAMPGO_RECORDINGS_DIR": (None, "recordings_dir"),
        "LAMPGO_SOCKET": (None, "socket_path"),
        "LAMPGO_WEB_HOST": ("web", "host"),
        "LAMPGO_WEB_PORT": ("web", "port"),
    }
    for env_key, (section, field) in env_map.items():
        value = os.environ.get(env_key)
        if value is None:
            continue
        if section is None:
            current = getattr(config, field)
            setattr(config, field, _coerce_env_value(current, value))
            if track:
                changed.append(field)
        else:
            sub = getattr(config, section)
            current = getattr(sub, field)
            setattr(sub, field, _coerce_env_value(current, value))
            if track:
                changed.append(f"{section}.{field}")

    # LAMPGO_API_BASE acts as a convenience for OpenClaw plugin alignment:
    # a single URL (e.g. http://127.0.0.1:18790) determines BOTH where lampgo
    # listens and where the OpenClaw plugin calls back. Individual LAMPGO_WEB_*
    # vars still win if set, to allow split host/port override.
    api_base = os.environ.get("LAMPGO_API_BASE", "").strip()
    if api_base:
        parsed = _parse_api_base(api_base)
        if parsed is not None:
            host, port = parsed
            if "LAMPGO_WEB_HOST" not in os.environ and host:
                config.web.host = host
                if track:
                    changed.append("web.host")
            if "LAMPGO_WEB_PORT" not in os.environ and port:
                config.web.port = port
                if track:
                    changed.append("web.port")
    return changed


def _parse_api_base(url: str) -> tuple[str, int] | None:
    """Parse a full URL or bare host:port into (host, port). Returns None on failure."""
    from urllib.parse import urlparse

    candidate = url if "://" in url else f"http://{url}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    host = parsed.hostname or ""
    port = parsed.port or 0
    if not port:
        return None
    # "127.0.0.1" binding shouldn't force lampgo to only-localhost listen;
    # leave host empty so the default 0.0.0.0 remains.
    return (host if host not in {"127.0.0.1", "localhost", "0.0.0.0"} else "", port)


def _coerce_env_value(current: object, raw: str) -> object:
    """Best-effort env var coercion based on the target field's current type."""
    if isinstance(current, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, Path):
        return Path(raw)
    return raw


def _deep_merge_dict(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _apply_cli_overrides(config: LampgoConfig, overrides: dict, *, track: bool = False) -> list[str]:
    """Apply flat key-value overrides from CLI arguments.

    Returns the list of dotted paths that were set (for provenance) when
    ``track=True``, else an empty list.
    """
    changed: list[str] = []
    for key, value in overrides.items():
        if value is None or value == "":
            continue
        if "." in key:
            section, field = key.split(".", 1)
            sub = getattr(config, section, None)
            if sub is not None:
                setattr(sub, field, value)
                if track:
                    changed.append(key)
        else:
            if hasattr(config, key):
                setattr(config, key, value)
                if track:
                    changed.append(key)
    return changed
