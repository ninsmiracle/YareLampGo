# lampgo Architecture

## Overview

lampgo is a single-process Python runtime for the LeLamp desktop robotic arm
(5-DOF Feetech STS3215 + ESP32 LED). It replaces the legacy `gemini_robotics` /
`lelamp-skills` codebases with a clean layered architecture and一个**级联式意图路由**设计。

## System Diagram

```
  ┌──────────────────────────────────────────────────┐
  │                  User Input                       │
  │  CLI / Web UI / Voice / IPC / Camera (multimodal) │
  └─────────────────────┬────────────────────────────┘
                        │
                 ┌──────▼──────┐
                 │ IntentRouter │
                 └──────┬──────┘
                        │
              ┌─────────▼─────────┐   hit
              │ ① Keyword Match    │──────────────────┐
              │    (<10 ms)        │                   │
              └─────────┬─────────┘                   │
                        │ miss                        │
              ┌─────────▼─────────┐   resolved        │
              │ ② LLM Agent Loop  │──────────────┐    │
              │  (multi-turn,     │               │    │
              │   tool calling)   │               │    │
              └─────────┬─────────┘               │    │
                        │ still complex           │    │
              ┌─────────▼─────────┐               │    │
              │ ③ OpenClaw        │               │    │
              │  (external agent, │               │    │
              │   human confirm)  │               │    │
              └─────────┬─────────┘               │    │
                        │                         │    │
                        └────────┬────────────────┘────┘
                                 │
                          ┌──────▼────────┐
                          │ SkillExecutor  │
                          │ (last-writer   │
                          │  -wins + FSM)  │
                          └──────┬────────┘
                                 │
                          ┌──────▼────────┐
                          │ MotionRuntime  │  dedicated thread, 50 Hz
                          │ (trapezoidal   │  stall detection
                          │  velocity)     │
                          └──────┬────────┘
                                 │
                          ┌──────▼────────┐
                          │ SafetyKernel   │  position limits, velocity caps, e-stop
                          └──────┬────────┘
                                 │
                          ┌──────▼────────┐
                          │      HAL       │  Feetech motor bus + ESP32 LED
                          └───────────────┘
```

## 级联式意图路由

路由是一个**降级链**，按顺序尝试，前一层命中就直接执行，不再往下：

| 层级 | 条件 | 延迟 | 示例 |
|------|------|------|------|
| ① Keyword match | 输入与关键词表精确匹配 | <10 ms | "点头" → `nod`, "回去" → `return_safe` |
| ② LLM Agent loop | 关键词未命中，交给多模态 LLM Agent 做 tool calling | 2–15 s | "站起来！" → `move_to(bp=0, ep=-85, wp=30)` |
| ③ OpenClaw | LLM Agent 也返回 `complex`（超出本地能力），降级到外部 agent | 5–30 s | 多步推理、需要人工确认的复杂任务 |

**Layer ② detail — LLM Agent Loop:**
- Model: `mimo-v2-omni` (OpenAI-compatible API)
- Multimodal: text + optional camera image (captured once at loop start)
- Multi-turn tool calling: up to `max_agent_turns` (default 8) rounds,
  `max_agent_tool_calls` (default 15) total calls
- 首轮 `tool_choice="required"` 强制调用工具，后续轮 `"auto"`
- Available tools: `move_to`, `look_at`, `set_expression`, `play_recording`,
  `capture_image`, `scan_and_capture`, `web_search`, `finish_response`
- System prompt includes joint guide, reference poses, and kinematic constraints
- Malformed tool call detection: 若 LLM 以文本形式返回工具调用（如 XML），自动检测并要求重试

## Motion Paradigms

MotionRuntime 对外暴露两种范式，选错会直接导致运动质量问题（抖动/顿挫）：

### 范式一：Goal-based（目标驱动）

**场景**：只知道终点坐标，由系统规划中间路径。

```
调用方  →  move_to(target, style)  →  TrajectoryPlan（easing曲线）  →  SafetyKernel.validate_frame  →  HAL
                                     或 _trapezoidal_step（linear）
```

| API | 用途 |
|-----|------|
| `ctx.motion.move_to(target)` | 单次点到点移动，返回 done_event，可 await |
| `ctx.motion.update_target(target, style="linear")` | 实时视觉伺服，高频推送目标，不等待完成 |

**适用**：LLM 指令的 `move_to`、`return_safe`、`look_at`、`presence_react` 的问候姿态。

**禁忌**：不要在循环里用 `move_to` 播放预录轨迹——每次调用都会清零关节速度并重建 TrajectoryPlan，导致每段起步前的微顿挫。

---

### 范式二：Trajectory-based（轨迹驱动）

**场景**：完整帧序列已知，系统直接逐帧执行，不做任何轨迹规划。

```
调用方  →  stream_frames(frames, fps)  →  SafetyKernel.clamp_positions（仅位置边界）  →  HAL
```

| API | 用途 |
|-----|------|
| `ctx.motion.stream_frames(frames, fps)` | 底层接口，返回 done_event |
| `ctx.play_frames(frames, fps)` | 封装了等待逻辑的高层接口（推荐） |

**适用**：
- CSV 录制回放（`play_recording`）——人手遥操作轨迹本身已含自然加减速，无需额外规划
- 参数化节奏动作（`nod`/`headshake`/`dance`/`idle_sway`）——由 `generate_waypoint_frames` / `generate_sine_frames` 预计算，easing 在生成时烘焙进帧

**禁忌**：不要把 CSV 帧下采样后再逐点 `move_to`——这是此前的错误用法，已删除。

---

### 速查表

| 你拥有的信息 | 正确范式 | API |
|---|---|---|
| 只有目标坐标 | Goal-based | `move_to` |
| 实时传感器反馈（视觉伺服） | Goal-based（线性） | `update_target(style="linear")` |
| 完整帧序列（录制/参数生成） | Trajectory-based | `stream_frames` / `play_frames` |

## Key Design Decisions

- **Control loop isolation**: MotionRuntime runs in a dedicated thread to avoid
  asyncio scheduling jitter. Communication via thread-safe queue.
- **Trapezoidal velocity profile**: Replaces the broken linear interpolation
  that caused stuttering. New targets can be injected without resetting velocity.
- **Hardware-based completion**: Motion is "done" when the *actually written*
  position (after safety clamping) reaches the target — not when the planner's
  internal trajectory converges. Includes stall detection (250 ticks with no
  actual progress → force complete with warning).
- **Safety as a gate**: Every motor command passes through SafetyKernel before
  reaching hardware. Joint limits, velocity caps, persistent e-stop.
- **Skills are the only way to move**: No raw joint commands exposed to callers.
  All motion goes through skills → MotionRuntime → SafetyKernel → HAL.
- **IPC-first**: CLI and OpenClaw use Unix socket IPC (JSON protocol) for <100 ms latency.
- **Web gateway (same process)**: Starlette app serves REST + WebSocket,
  subscribes to EventBus for real-time status push to the browser chat UI.

## Module Map

### Core (`lampgo/core/`)

| Module | Responsibility |
|--------|----------------|
| `types.py` | Foundation types: `JointState`, `MotionTarget`, `MotionStatus`, `SkillResult` |
| `config.py` | Pydantic config models + load chain (CLI → env → `.env` → `lampgo.toml` → defaults) |
| `hal.py` | Hardware abstraction: wraps lerobot `FeetechMotorsBus`, calibration, read/write |
| `safety.py` | SafetyKernel: joint limits, per-tick velocity caps, persistent e-stop, bus health |
| `motion.py` | MotionRuntime: trapezoidal interpolation, stall detection, dedicated control thread |
| `led.py` | LEDController: ESP32 serial protocol for expressions |
| `events.py` | Typed in-process event bus (`SkillStarted`, `SkillFinished`, `SafetyTriggered`, …) |

### Skills (`lampgo/skills/`)

| Module | Responsibility |
|--------|----------------|
| `base.py` | `Skill` abstract base, `SkillContext`, `ParameterSpec` |
| `registry.py` | `SkillRegistry`: register, lookup, list skills |
| `executor.py` | `SkillExecutor`: run skill, cancel/timeout, priority (`estop` > `return_safe` > others) |
| `fsm.py` | Device state machine (Idle / Executing / SafeStop / Recovering) |
| `recorder.py` | `TeachRecorder`: record joint trajectories to CSV, smoothing, compression |
| `builtin/motion_skills.py` | `MoveToSkill`, `ReturnSafeSkill`, `EStopSkill`, `SAFE_POSITION` |
| `builtin/parametric_skills.py` | `NodSkill`, `HeadShakeSkill`, `LookAtSkill`, `IdleSwaySkill`, `DanceSkill` |
| `builtin/playback_skills.py` | `PlayRecordingSkill`: CSV frame-stream playback with auto return-safe |
| `builtin/expression_skills.py` | `SetExpressionSkill`: LED mode commands |
| `builtin/reactive_skills.py` | Event-driven reactive skills (yield to foreground) |
| `builtin/teleop_skills.py` | Teleoperation: joint ↔ desktop input mapping |

### Perception (`lampgo/perception/`)

| Module | Responsibility |
|--------|----------------|
| `router.py` | `IntentRouter`: keyword table → LLM agent → OpenClaw escalation |
| `llm_client.py` | `LLMClient`: async multi-turn agent loop, tool calling, multimodal (text + camera) |
| `camera.py` | `CameraCapture`: OpenCV USB camera, resolution/quality config, base64 JPEG |
| `presence.py` | Lightweight OpenCV human presence detection → `PresenceDetected` event |
| `audio.py` | Perception-side VAD stub → `VoiceActivity` event |

### Voice (`lampgo/voice/`)

| Module | Responsibility |
|--------|----------------|
| `loop.py` | `VoiceLoop`: listen → VAD → STT → route → skill/TTS async loop |
| `audio.py` | Microphone capture & speaker playback via `sounddevice` |
| `stt.py` | `WhisperSTT`: OpenAI Whisper API |
| `tts.py` | `EdgeTTS`: Microsoft edge-tts |
| `vad.py` | `EnergyVAD`: energy-threshold voice activity detection |

### Bridge (`lampgo/bridge/`)

| Module | Responsibility |
|--------|----------------|
| `openclaw.py` | `OpenClawAdapter`: expose skills to OpenClaw, human confirmation UI flow |
| `desktop.py` | `DesktopBridge` / `InputBackend`: desktop control abstraction (PyAutoGUI) |

### Web (`lampgo/web/`)

| Module | Responsibility |
|--------|----------------|
| `gateway.py` | Starlette app: REST API (`/api/text`, `/api/invoke`, …) + WebSocket + static UI |
| `ws_bridge.py` | `WSBridge`: EventBus → WebSocket JSON broadcast |
| `static/` | Zero-build browser chat UI (HTML + JS + CSS) |

### Top-level

| Module | Responsibility |
|--------|----------------|
| `server.py` | `LampgoServer`: assembles all subsystems, main async entry point |
| `cli.py` | CLI: `run`, `invoke`, `text`, `move`, `play`, `skills`, `status`, `detect`, `estop`, `calibrate`, `record`, `help` |
| `ipc.py` | Unix socket IPC server + synchronous client (JSON-line protocol) |
| `autodetect.py` | Serial port auto-detection for Feetech motors & ESP32 LED |

## Directory Layout

```
lampgo/
├── assets/
│   ├── calibration/          # Per-device calibration JSON
│   └── recordings/           # 37 pre-recorded CSV motion clips
├── docs/                     # Architecture, plans, project description
├── examples/                 # Usage examples (basic_motion, custom_skill, openclaw)
├── lampgo/                   # Main Python package
│   ├── core/                 # HAL, motion, safety, config, events, LED
│   ├── perception/           # Intent routing, LLM agent, camera, presence
│   ├── skills/               # Skill framework + built-in skills
│   │   └── builtin/          # motion, parametric, playback, expression, reactive, teleop
│   ├── voice/                # Voice loop, STT, TTS, VAD
│   ├── bridge/               # OpenClaw adapter, desktop bridge
│   ├── web/                  # Starlette gateway + static chat UI
│   ├── server.py             # Main daemon
│   ├── cli.py                # CLI interface
│   ├── ipc.py                # Unix socket IPC
│   └── autodetect.py         # Serial port detection
├── openclaw-skills/          # 4 OpenClaw skill packages
│   ├── lampgo/               # Core skill (SKILL.md + references: joints, actions, LED)
│   ├── lampgo-codegen/       # Code generation skill
│   ├── lampgo-search-touch/  # Search & touch skill
│   └── lampgo-vision/        # Vision skill (snap + analyze)
├── tests/                    # 22 test modules + conftest (MockHAL)
├── pyproject.toml            # Dependencies, entry points, build config
├── lampgo.toml               # Runtime config (device, motion, safety, LLM, camera)
└── .env                      # Secrets (API keys, ports, timeouts)
```

## Configuration Hierarchy

```
CLI flags  →  Environment variables  →  .env file  →  lampgo.toml  →  Defaults
(highest)                                                              (lowest)
```

Key env vars:
- `LAMPGO_LLM_API_KEY`, `LAMPGO_LLM_API_BASE`, `LAMPGO_LLM_MODEL` — LLM connection
- `LAMPGO_LLM_TIMEOUT_S` — LLM request timeout (default 60)
- `LAMPGO_LLM_MAX_AGENT_TURNS`, `LAMPGO_LLM_MAX_AGENT_TOOL_CALLS` — agent loop limits
- `LAMPGO_CAMERA_PORT` — USB camera device index (e.g. `0`)
- `LAMPGO_MOTOR_PORT`, `LAMPGO_LED_PORT` — serial ports

## Hardware

- **Motors**: 5× Feetech STS3215 servos on a single serial bus
  - `base_yaw` (−100°~100°): body rotation
  - `base_pitch` (−100°~100°): body tilt forward/backward
  - `elbow_pitch` (−90°~100°): arm bend/extend
  - `wrist_roll` (−75°~75°): lamp head roll
  - `wrist_pitch` (−45°~100°): lamp head tilt = camera angle
- **LED**: ESP32 serial-controlled expression display
- **Camera**: USB camera (optional, for multimodal LLM vision)

## Dependencies

Core: `pydantic`, `structlog`, `pyserial`, `python-dotenv`, `lerobot[feetech]`,
`sounddevice`, `edge-tts`, `httpx`, `starlette`, `uvicorn`, `websockets`

Optional: `opencv-python` (perception), `pyautogui` (desktop bridge)

Dev: `pytest`, `pytest-asyncio`, `ruff`

Entry point: `lampgo = "lampgo.cli:main"` → `uv run lampgo <command>`