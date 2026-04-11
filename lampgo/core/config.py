"""Configuration models for lampgo.

Config loading priority (highest wins):
  1. CLI arguments
  2. Environment variables (LAMPGO_MOTOR_PORT, etc.)
  3. .env file in project root
  4. lampgo.toml config file
  5. Built-in defaults
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


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
    lamp_id: str = Field(default="AL01", description="Device identity used for calibration file lookup")
    motors: dict[str, MotorConfig] = Field(default_factory=lambda: dict(DEFAULT_MOTORS))
    use_degrees: bool = Field(default=True, description="Interpret positions as degrees (vs normalised -100..100)")
    disable_torque_on_disconnect: bool = True
    calibration_dir: Path = Field(default=Path("assets/calibration"))


class MotionConfig(BaseModel):
    """Motion runtime tuning parameters."""

    tick_rate_hz: float = Field(default=50.0, gt=0, description="Control loop frequency")
    default_max_velocity: float = Field(default=120.0, gt=0, description="Degrees per second per joint")
    default_max_acceleration: float = Field(default=600.0, gt=0, description="Degrees per second^2 per joint")
    default_style: str = Field(
        default="gentle",
        description="Biomimetic style preset when MotionTarget.style is unset (gentle|confident|curious|bouncy|hesitant|linear)",
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
        default=0.8,
        ge=0,
        description="Peak breathing oscillation amplitude in degrees.",
    )

    # --- Overlapping Action (secondary joint coupling) ---
    overlapping_action: bool = Field(
        default=True,
        description="Enable secondary joints to echo primary joint motion with a delay and reduced amplitude.",
    )


class SafetyConfig(BaseModel):
    """Safety kernel limits."""

    joint_limits: dict[str, JointLimits] = Field(default_factory=lambda: dict(DEFAULT_JOINT_LIMITS))
    max_velocity: float = Field(default=180.0, gt=0, description="Hard velocity cap (deg/s)")
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

    provider: str = Field(default="openai", description="LLM provider: openai, anthropic, gemini, local")
    model: str = Field(default="gpt-4o-mini", description="Model name for complex reasoning tasks")
    fast_model: str = Field(default="gpt-4o-mini", description="Model for simple/fast intent classification")
    api_key: str = Field(default="", description="Set via LAMPGO_LLM_API_KEY env var, NOT in config file")
    api_base: str = Field(default="", description="Custom API base URL (for local/proxy models)")
    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int = Field(default=512, gt=0)
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
    """Load configuration with the priority chain: CLI > env > .env > toml > defaults.

    Args:
        config_path: Explicit path to lampgo.toml. If None, searches project root.
        env_file: Explicit path to .env. If None, searches project root.
        cli_overrides: Dict of flat overrides from CLI args (e.g. {"device.motor_port": "/dev/ttyUSB0"}).
    """
    project_root = _find_project_root()

    # 1. Load .env file (populates os.environ for secrets)
    if env_file is None:
        env_file = project_root / ".env"
    if Path(env_file).exists():
        load_dotenv(env_file)

    # 2. Load lampgo.toml
    toml_data: dict = {}
    if config_path is None:
        config_path = project_root / "lampgo.toml"
    if Path(config_path).exists():
        with open(config_path, "rb") as f:
            toml_data = tomllib.load(f)

    # 3. Build config from TOML
    config = LampgoConfig(**toml_data) if toml_data else LampgoConfig()

    # 4. Apply environment variable overrides
    _apply_env_overrides(config)

    # 5. Apply CLI overrides
    if cli_overrides:
        _apply_cli_overrides(config, cli_overrides)

    return config


def _apply_env_overrides(config: LampgoConfig) -> None:
    """Override config fields from LAMPGO_* environment variables."""
    env_map = {
        "LAMPGO_MOTOR_PORT": ("device", "motor_port"),
        "LAMPGO_LED_PORT": ("device", "led_port"),
        "LAMPGO_LAMP_ID": ("device", "lamp_id"),
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
    }
    for env_key, (section, field) in env_map.items():
        value = os.environ.get(env_key)
        if value is None:
            continue
        if section is None:
            current = getattr(config, field)
            setattr(config, field, _coerce_env_value(current, value))
        else:
            sub = getattr(config, section)
            current = getattr(sub, field)
            setattr(sub, field, _coerce_env_value(current, value))


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


def _apply_cli_overrides(config: LampgoConfig, overrides: dict) -> None:
    """Apply flat key-value overrides from CLI arguments."""
    for key, value in overrides.items():
        if value is None or value == "":
            continue
        if "." in key:
            section, field = key.split(".", 1)
            sub = getattr(config, section, None)
            if sub is not None:
                setattr(sub, field, value)
        else:
            if hasattr(config, key):
                setattr(config, key, value)
