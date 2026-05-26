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
from typing import Any, ClassVar, Literal

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

    # --- Idle breathing (deprecated) ---
    # Kept for backwards compatibility with older local config files/tests.
    # Runtime no longer applies continuous micro-motion from the control loop;
    # idle presence is now driven by the idle_sway factory skill scheduler.
    breathing_enabled: bool = Field(
        default=False,
        description="Deprecated. Continuous idle micro-motion is disabled; use idle_sway_* settings instead.",
    )
    breathing_amplitude: float = Field(
        default=3.0,
        ge=0,
        description="Deprecated. Legacy breathing amplitude retained only for old config compatibility.",
    )

    # --- Idle random sway scheduler ---
    idle_sway_enabled: bool = Field(
        default=True,
        description="Enable occasional idle_sway factory-skill triggers after the lamp has been idle.",
    )
    idle_sway_idle_after_s: float = Field(
        default=600.0,
        ge=0,
        description="How long the lamp must be idle before automatic idle_sway can trigger.",
    )
    idle_sway_interval_s: float = Field(
        default=30.0,
        gt=0,
        description="Base interval between automatic idle_sway triggers once the lamp is idle.",
    )
    idle_sway_interval_jitter_s: float = Field(
        default=8.0,
        ge=0,
        description="Random +/- jitter applied to idle_sway_interval_s.",
    )
    idle_sway_duration_s: float = Field(
        default=8.0,
        gt=0,
        description="Duration passed to each automatic idle_sway skill invocation.",
    )
    idle_sway_amplitude: float = Field(
        default=6.0,
        ge=0,
        description="Amplitude in degrees passed to each automatic idle_sway skill invocation.",
    )
    idle_sway_period_s: float = Field(
        default=4.5,
        gt=0,
        description="Period in seconds passed to each automatic idle_sway skill invocation.",
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
        # Migration: earlier builds briefly exposed `mimo-anthropic` as a
        # separate provider preset.  That turned out to be wrong — the
        # provider IS MiMo, what varies is just the message_type.  Users
        # who saved the short-lived key get normalised back to `mimo`,
        # and their persisted `message_type: "anthropic"` keeps them on
        # the Anthropic endpoint automatically.
        "mimo-anthropic": "mimo",
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
    model: str = Field(default="mimo-v2.5", description="Model name for complex reasoning tasks")
    fast_model: str = Field(default="mimo-v2.5", description="Model for simple/fast intent classification")
    api_key: str = Field(default="", description="Set via LAMPGO_LLM_API_KEY env var, NOT in config file")
    api_base: str = Field(default="", description="Custom API base URL (for local/proxy models)")
    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int = Field(
        default=20000,
        gt=0,
        description=(
            "单次对话回复的 token 上限（OpenAI: max_tokens；MiMo: max_completion_tokens）。"
            "对话链若支持多轮工具调用，建议 ≥ 2048。默认 20000 适配 mimo-v2.5 这类新一代长输出模型。"
        ),
    )
    summary_max_tokens: int = Field(
        default=20000,
        gt=0,
        description=(
            "每日记忆 / 会话摘要任务的输出上限。推理模型（mimo-v2-omni、o-系列、deepseek-r1）"
            "会把预算花在思考链上，至少给 4096 才能稳定产出 bullet；默认 20000 保证即使强推理模型"
            "也能吐出完整的摘要段落。"
        ),
    )
    context_window: int = Field(
        default=200000,
        gt=0,
        description=(
            "模型的输入上下文窗口（tokens）。目前仅作信息记录；将来用于在超限前自动裁剪历史消息。"
            "按所用模型填：mimo-v2.5 ≈ 200k、Claude Sonnet ≈ 200k、gpt-4o-mini ≈ 128k、"
            "mimo-v2-pro/omni ≈ 128k。"
        ),
    )
    timeout_s: float = Field(default=300.0, gt=0, description="HTTP timeout for LLM requests")
    history_turns: int = Field(
        default=30,
        ge=0,
        le=200,
        description=(
            "每次调用 LLM 时附带的当前会话历史轮数上限（一轮 = 一次 user+assistant 交互）。"
            "0 表示关闭短期上下文（每轮对话互相独立）。过大可能超出模型 context_window，"
            "运行时会从最旧消息开始裁剪。"
        ),
    )
    enable_thinking: bool = Field(
        default=False,
        description="默认是否允许聊天请求开启模型思考过程输出。建议仅调试推理模型时开启。",
    )
    # -------------------------------------------------------------------
    # Web search — MiMo-only, implemented as an **independent sub-service**.
    # -------------------------------------------------------------------
    #
    # Design note (important, read before touching):
    #
    # Web search is exposed to the agent as a plain ``function`` tool named
    # ``web_search``.  The agent/runtime decides when to call it; the lamp
    # itself executes each call by opening a **dedicated** HTTP connection
    # to MiMo's OpenAI-compatible ``chat.completions`` endpoint and handing
    # MiMo its **private** ``{"type":"web_search"}`` tool type.  That tool
    # type does NOT exist on any other provider — not on OpenAI, not on
    # Anthropic, not on real Anthropic's ``/v1/messages`` surface.
    #
    # Consequences of that design, encoded here:
    #
    # * Web search is always **MiMo OpenAI-compat wire format** regardless
    #   of what the primary LLM ``provider`` / ``message_type`` is set to.
    #   You can run the main loop against Anthropic / OpenAI / local Ollama
    #   and still get web search, as long as the user provides a MiMo key.
    # * Credentials are intentionally **separate** from ``api_key``.  If
    #   ``web_search_api_key`` is empty we fall back to reusing ``api_key``
    #   **only when** the main ``provider`` is ``mimo`` (i.e. that same key
    #   is already known to be a MiMo key).  For any other provider the
    #   user must supply a dedicated MiMo key or the feature stays off.
    # * The base URL and model are deliberately **not** user-configurable:
    #   the endpoint is fixed at ``https://api.mimomimo.com/v1`` and the
    #   model at ``mimo-v2.5-pro`` (see ``MIMO_WEB_SEARCH_BASE_URL`` /
    #   ``MIMO_WEB_SEARCH_MODEL`` in :mod:`lampgo.perception.llm_client`).
    #   Exposing them as settings would invite users to break the feature
    #   by pointing it at an endpoint that doesn't implement MiMo's private
    #   tool type.
    web_search_enabled: bool = Field(
        default=True,
        description=(
            "启用独立的 MiMo 联网搜索子服务。关闭后 web_search 工具不会暴露给 LLM。"
        ),
    )
    web_search_api_key: str = Field(
        default="",
        description=(
            "MiMo 联网搜索专用 API key（OpenAI-compat）。留空时，若主 LLM provider=mimo "
            "则自动复用 LLMConfig.api_key，否则本功能被静默禁用（不会注册 web_search 工具）。"
            "与主 LLM key 一样，建议通过 credentials.json 持久化而不是写进 config 文件。"
        ),
    )
    web_search_force: bool = Field(
        default=False,
        description="True = 对每次调用都强制 force_search；False = 交给模型自行判断。",
    )
    web_search_limit: int = Field(default=3, ge=1, le=10, description="单次联网搜索最多使用的网页数")
    web_search_max_keyword: int = Field(default=3, ge=1, le=10, description="单次联网搜索最多使用的关键词数")
    web_search_country: str = Field(default="", description="近似地理位置-国家（用于搜索结果本地化，可留空）")
    web_search_region: str = Field(default="", description="近似地理位置-省/州（用于搜索结果本地化，可留空）")
    web_search_city: str = Field(default="", description="近似地理位置-城市（用于搜索结果本地化，可留空）")
    max_agent_turns: int = Field(default=20, ge=1, le=50, description="Max LLM agent loop turns")
    max_agent_tool_calls: int = Field(default=50, ge=1, le=100, description="Max total tool calls per agent loop")


class CameraConfig(BaseModel):
    """Camera capture settings used for LLM vision input."""

    port: str = Field(default="", description="Camera device index or path (empty = disabled)")


class DeviceEsp32Config(BaseModel):
    """Wireless camera/mic device (XIAO ESP32S3 Sense running lampgo-cam firmware).

    Semantics of ``enabled``: *prefer* ESP32 over local. When the device is
    discovered via mDNS and reachable, perception pulls frames/audio from it.
    When it's not reachable at cold start, lampgo silently falls back to the
    local camera (``camera.port``) and microphone (``voice.mic_device``) and
    the Web UI shows a banner. Runtime disconnects do NOT auto-fallback — the
    LLM sees "no frame" and can respond naturally, to avoid mid-conversation
    source flips.
    """

    enabled: bool = Field(
        default=False,
        description="Prefer ESP32 device over local camera/mic when available.",
    )
    preferred_host: str = Field(
        default="",
        description=(
            "Optional mDNS hostname to pin (e.g. 'lampgo-cam-AB12.local'). "
            "Empty = auto-discover first reachable lampgo-cam device."
        ),
    )
    jpeg_quality: int = Field(
        default=10,
        ge=4,
        le=63,
        description="Camera JPEG quality sent to ESP32 (4=best, 63=worst).",
    )
    framesize: int = Field(
        default=8,
        ge=0,
        le=13,
        description="ESP32 framesize enum (0=96x96 … 8=SVGA 800x600 … 13=UXGA 1600x1200).",
    )
    mic_enabled: bool = Field(
        default=False,
        description="Enable ESP32 PDM microphone stream (still falls back to sounddevice when offline).",
    )
    http_timeout_s: float = Field(
        default=5.0,
        gt=0,
        le=30.0,
        description="HTTP timeout when proxying to ESP32 (seconds).",
    )


class VoiceConfig(BaseModel):
    """Voice / TTS / STT configuration."""

    stt_provider: str = Field(default="volcengine", description="STT provider: volcengine")
    stt_model: str = Field(default="bigmodel", description="Volcengine ASR model name")
    tts_provider: str = Field(default="volcengine", description="TTS provider: volcengine, edge-tts")
    tts_model: str = Field(
        default="",
        description=(
            "Optional Volcengine TTS model id (e.g. seed-tts-2.0-standard). "
            "Ignored by edge-tts."
        ),
    )
    tts_voice: str = Field(default="zh_female_vv_uranus_bigtts", description="TTS voice identifier")
    tts_style_prompt: str = Field(default="", description="Reserved TTS style instruction")
    chat_model: str = Field(default="mimo-v2-pro", description="LLM model for voice chat streaming responses")
    mic_device: str = Field(default="", description="Microphone device index or name (empty = system default)")
    wake_word: str = Field(default="", description="Wake word for hands-free activation (empty = disabled)")
    vad_enabled: bool = Field(default=False, description="Enable voice activity detection")
    livekit_url: str = Field(default="ws://127.0.0.1:7880", description="LiveKit server WebSocket URL")
    livekit_api_key: str = Field(default="devkey", description="LiveKit API key for token signing")
    livekit_api_secret: str = Field(default="secret", description="LiveKit API secret for token signing")
    livekit_room: str = Field(default="lampgo", description="LiveKit room name for voice conversations")
    livekit_agent_name: str = Field(
        default="mimo-agent-lampgo-jarvis",
        description="Agent name dispatched in the LiveKit room (must match roles.yaml name_prefix + voice_agent).",
    )
    call_mode: Literal["stable", "interruptible", "esp32_aec"] = Field(
        default="stable",
        description="LiveKit call mode: stable half-duplex, interruptible without ESP32 AEC, or experimental ESP32 AEC.",
    )
    livekit_allow_interruptions: bool = Field(
        default=False,
        description="Allow users to barge in during LiveKit RTC conversations and interrupt current playback/LLM turn.",
    )
    echo_gate_hangover_ms: int = Field(
        default=1000,
        ge=0,
        le=5000,
        description="Stable-mode ESP32 mic mute hangover after speaker voice energy, in milliseconds.",
    )
    echo_text_filter_enabled: bool = Field(
        default=True,
        description="Drop likely self-echo ASR text in interruptible modes without enabling ESP32-side AEC.",
    )
    silence_timeout_s: int = Field(default=60, ge=10, le=300, description="Seconds of silence before ending a conversation")
    volcengine_app_id: str = Field(default="", description="Volcengine app ID for ASR/TTS")
    volcengine_access_token: str = Field(default="", description="Volcengine access token for ASR/TTS")
    livekit_tts_voice: str = Field(
        default="zh_female_vv_uranus_bigtts",
        description="Deprecated compatibility field; LiveKit conversations use tts_voice.",
    )

    @field_validator("stt_provider", "tts_provider", mode="before")
    @classmethod
    def _normalize_legacy_voice_provider(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        s = v.strip().lower()
        if s in {"mimo", "mimo-tts", "mimo-stt"}:
            return "volcengine"
        return s

    @field_validator("call_mode", mode="before")
    @classmethod
    def _normalize_call_mode(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        s = v.strip().lower().replace("-", "_")
        aliases = {
            "safe": "stable",
            "half_duplex": "stable",
            "barge_in": "interruptible",
            "interrupt": "interruptible",
            "interruptions": "interruptible",
            "aec": "esp32_aec",
            "experimental_aec": "esp32_aec",
        }
        return aliases.get(s, s)

    @field_validator("wake_word", mode="before")
    @classmethod
    def _normalize_wake_word(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        s = v.strip()
        if not s:
            return ""
        if s.lower() in {"0", "false", "off", "none", "disabled"}:
            return ""
        return "wn9_hixiaoxing_tts"

    @field_validator("stt_model", mode="before")
    @classmethod
    def _normalize_legacy_stt_model(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        s = v.strip()
        if s in {"mimo-v2.5", "mimo-v2-omni"}:
            return "bigmodel"
        return s

    @field_validator("tts_model", mode="before")
    @classmethod
    def _normalize_legacy_tts_model(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        s = v.strip()
        if s in {"mimo-v2.5-tts", "mimo-v2-tts"}:
            return ""
        return s

    @field_validator("tts_voice", "livekit_tts_voice", mode="before")
    @classmethod
    def _normalize_legacy_tts_voice(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        from lampgo.voice.tts import _volcengine_voice_or_default

        s = v.strip()
        if s == "BV700_streaming":
            return "zh_female_vv_uranus_bigtts"
        return _volcengine_voice_or_default(s)

    @field_validator(
        "livekit_url",
        "livekit_api_key",
        "livekit_api_secret",
        "livekit_room",
        "livekit_agent_name",
        "volcengine_app_id",
        "volcengine_access_token",
        "wake_word",
        mode="before",
    )
    @classmethod
    def _strip_voice_runtime_strings(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v


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
    device_esp32: DeviceEsp32Config = Field(default_factory=DeviceEsp32Config)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    recordings_dir: Path = Field(default=Path("assets/recordings"))
    socket_path: str = Field(default="/tmp/lampgo.sock", description="Unix socket path for IPC")
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
            ws_key = str(creds.get("llm_web_search_api_key") or "").strip()
            if ws_key:
                config.llm.web_search_api_key = ws_key
                provenance["llm.web_search_api_key"] = "credentials"
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
        "LAMPGO_LLM_ENABLE_THINKING": ("llm", "enable_thinking"),
        "LAMPGO_LLM_TIMEOUT_S": ("llm", "timeout_s"),
        "LAMPGO_LLM_WEB_SEARCH_ENABLED": ("llm", "web_search_enabled"),
        "LAMPGO_LLM_WEB_SEARCH_API_KEY": ("llm", "web_search_api_key"),
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
        "LAMPGO_VOICE_TTS_MODEL": ("voice", "tts_model"),
        "LAMPGO_VOICE_TTS_VOICE": ("voice", "tts_voice"),
        "LAMPGO_VOICE_TTS_STYLE_PROMPT": ("voice", "tts_style_prompt"),
        "LAMPGO_VOICE_CHAT_MODEL": ("voice", "chat_model"),
        "LAMPGO_VOICE_MIC_DEVICE": ("voice", "mic_device"),
        "LAMPGO_VOICE_WAKE_WORD": ("voice", "wake_word"),
        "LAMPGO_VOICE_LIVEKIT_URL": ("voice", "livekit_url"),
        "LAMPGO_VOICE_LIVEKIT_API_KEY": ("voice", "livekit_api_key"),
        "LAMPGO_VOICE_LIVEKIT_API_SECRET": ("voice", "livekit_api_secret"),
        "LAMPGO_VOICE_LIVEKIT_ROOM": ("voice", "livekit_room"),
        "LAMPGO_VOICE_CALL_MODE": ("voice", "call_mode"),
        "LAMPGO_VOICE_LIVEKIT_ALLOW_INTERRUPTIONS": ("voice", "livekit_allow_interruptions"),
        "LAMPGO_VOICE_ECHO_GATE_HANGOVER_MS": ("voice", "echo_gate_hangover_ms"),
        "LAMPGO_VOICE_ECHO_TEXT_FILTER_ENABLED": ("voice", "echo_text_filter_enabled"),
        "LAMPGO_VOICE_SILENCE_TIMEOUT_S": ("voice", "silence_timeout_s"),
        "LAMPGO_VOICE_VOLCENGINE_APP_ID": ("voice", "volcengine_app_id"),
        "LAMPGO_VOICE_VOLCENGINE_ACCESS_TOKEN": ("voice", "volcengine_access_token"),
        "LAMPGO_VOICE_LIVEKIT_TTS_VOICE": ("voice", "livekit_tts_voice"),
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
