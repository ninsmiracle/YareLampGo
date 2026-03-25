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


class VoiceConfig(BaseModel):
    """Voice / TTS / STT configuration.

    Used for real-time voice conversation (future milestone).
    """

    stt_provider: str = Field(default="", description="STT provider: whisper, azure, local (empty = disabled)")
    tts_provider: str = Field(default="", description="TTS provider: edge-tts, azure, local (empty = disabled)")
    tts_voice: str = Field(default="zh-CN-XiaoxiaoNeural", description="TTS voice identifier")
    wake_word: str = Field(default="", description="Wake word for hands-free activation (empty = disabled)")
    vad_enabled: bool = Field(default=False, description="Enable voice activity detection")


class LampgoConfig(BaseModel):
    """Root configuration combining all sub-configs."""

    device: DeviceConfig = Field(default_factory=DeviceConfig)
    motion: MotionConfig = Field(default_factory=MotionConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    led: LEDConfig = Field(default_factory=LEDConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    recordings_dir: Path = Field(default=Path("assets/recordings"))
    socket_path: str = Field(default="/tmp/lampgo.sock", description="Unix socket path for IPC")
    voice_enabled: bool = Field(default=False, description="Enable voice loop on startup")


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
        "LAMPGO_VOICE_STT_PROVIDER": ("voice", "stt_provider"),
        "LAMPGO_VOICE_TTS_PROVIDER": ("voice", "tts_provider"),
        "LAMPGO_VOICE_TTS_VOICE": ("voice", "tts_voice"),
        "LAMPGO_RECORDINGS_DIR": (None, "recordings_dir"),
        "LAMPGO_SOCKET": (None, "socket_path"),
    }
    for env_key, (section, field) in env_map.items():
        value = os.environ.get(env_key)
        if value is None:
            continue
        if section is None:
            setattr(config, field, Path(value) if field.endswith("_dir") else value)
        else:
            sub = getattr(config, section)
            setattr(sub, field, value)


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
