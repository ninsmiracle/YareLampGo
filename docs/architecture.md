# lampgo Architecture

## Overview

lampgo is a single-process Python runtime for the LeLamp desktop robotic arm.
It replaces both the `gemini_robotics` and `lelamp-skills` legacy codebases with
a clean layered architecture and a **dual-path design** for fast local responses
and powerful OpenClaw integration.

## Dual-Path Architecture

```
Fast Path (sub-1s, lampgo local)         Complex Path (5-30s, OpenClaw)
   Microphone / CLI / text                   OpenClaw App
         |                                        |
    STT (Whisper API)                       Claude Opus LLM
         |                                        |
    IntentRouter                            SKILL.md x4
      |         |                                 |
  Keyword    Fast LLM                             |
  Match     (gpt-4o-mini)                         |
      |         |                                 |
      +----+----+                                 |
           |                                      |
      Web UI (Browser)                            |
        |  WebSocket + REST                       |
        |                                         |
     WebGateway (Starlette, same process)         |
        |  direct method call                     |
        |                                         |
        +--- IPC (Unix Socket) --+----------------+
                               |
                          SkillExecutor + FSM
                               |
                         MotionRuntime (50Hz, dedicated thread)
                               |
                          SafetyKernel
                               |
                             HAL
                               |
                    Hardware (Feetech STS3215 + ESP32 LED)
```

## Key Design Decisions

- **Control loop isolation**: MotionRuntime runs in a dedicated thread to avoid
  asyncio scheduling jitter. Communication via thread-safe queue.
- **Trapezoidal velocity profile**: Replaces the broken linear interpolation that
  caused stuttering. New targets can be injected without resetting velocity.
- **Safety as a gate**: Every motor command passes through SafetyKernel before
  reaching hardware. Joint limits, velocity caps, persistent e-stop.
- **Skills are the only way to move**: No raw joint commands exposed to callers.
  All motion goes through skills -> MotionRuntime -> SafetyKernel -> HAL.
- **Simple FSM, not BT**: Idle/Executing/SafeStop/Recovering states. Behavior
  trees deferred until complexity demands it.
- **IPC-first**: CLI and OpenClaw use Unix socket IPC (JSON protocol) for <100ms latency.
- **Web gateway (same process)**: Starlette app serves REST + WebSocket, subscribes
  to EventBus for real-time step-by-step status push to the browser chat UI.
- **Dual-path intent routing**: Fast path (keyword + fast LLM) for sub-1s response,
  complex path (OpenClaw + Claude Opus) for multi-step reasoning.

## Module Map

| Module | Responsibility |
|--------|----------------|
| `core/types.py` | Foundation types (JointState, MotionTarget, etc.) |
| `core/config.py` | Pydantic configuration models + load chain |
| `core/hal.py` | Hardware abstraction (motor bus I/O) |
| `core/safety.py` | Safety kernel (limits, e-stop, bus health) |
| `core/motion.py` | Motion runtime (trapezoidal interpolation, control thread) |
| `core/led.py` | ESP32 LED controller |
| `core/events.py` | Typed event bus |
| `ipc.py` | Unix socket IPC server + synchronous client |
| `autodetect.py` | Serial port auto-detection |
| `skills/base.py` | Skill base class and context |
| `skills/registry.py` | Skill registration and lookup |
| `skills/executor.py` | Skill execution with cancel/timeout |
| `skills/fsm.py` | Device state machine |
| `skills/builtin/` | Built-in skills (move_to, play, nod, etc.) |
| `skills/recorder.py` | Teach recording, smoothing, compression |
| `perception/router.py` | IntentRouter (keyword + LLM fallback) |
| `perception/llm_client.py` | Fast LLM client (OpenAI-compatible function calling) |
| `voice/loop.py` | Voice interaction loop (listen-route-act-speak) |
| `voice/stt.py` | Whisper API speech-to-text |
| `voice/tts.py` | edge-tts text-to-speech |
| `voice/vad.py` | Energy-based voice activity detection |
| `voice/audio.py` | Microphone capture via sounddevice |
| `bridge/openclaw.py` | OpenClaw adapter |
| `bridge/desktop.py` | PC Bridge (mouse, keyboard, apps) |
| `web/gateway.py` | Starlette web gateway (REST + WebSocket + static UI) |
| `web/ws_bridge.py` | EventBus → WebSocket broadcast bridge |
| `web/static/` | Browser chat UI (HTML + JS + CSS, zero-build) |
| `server.py` | Main daemon entry point |
| `cli.py` | CLI interface (IPC-first) |
| `openclaw-skills/` | 4 OpenClaw skill packages |
