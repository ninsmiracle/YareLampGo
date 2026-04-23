"""Main entry point — creates all components and runs the asyncio loop.

The server owns:
  - Hardware (HAL, LED, Motion, Safety)
  - Skill system (Registry, Executor, FSM)
  - IPC server (Unix socket for CLI / OpenClaw / scripts)
  - IntentRouter (keyword + optional fast LLM)
"""

from __future__ import annotations

import asyncio
import re
import signal
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

from lampgo.bridge.openclaw import OpenClawAdapter
from lampgo.bridge.state_writer import MinimalState, StateWriter
from lampgo.core.config import LampgoConfig, LEDConfig
from lampgo.core.events import (
    AgentFinished,
    ChatMessage,
    EventBus,
    IntentProgress,
    IntentRouting,
    OpenClawAskRequested,
    OpenClawAskResolved,
    ToolCallFinished,
    ToolCallPlanned,
    TtsAudio,
)
from lampgo.core.hal import HardwareAbstraction
from lampgo.core.led import LEDController
from lampgo.core.motion import MotionRuntime
from lampgo.core.safety import SafetyKernel
from lampgo.ipc import IPCServer
from lampgo.perception.router import IntentRouter, IntentType
from lampgo.skills.base import SkillContext
from lampgo.skills.builtin.expression_skills import SetExpressionSkill
from lampgo.skills.builtin.motion_skills import EStopSkill, MoveToSkill, ReturnSafeSkill
from lampgo.skills.builtin.parametric_skills import (
    DanceSkill,
    HeadShakeSkill,
    IdleSwaySkill,
    LookAtSkill,
    NodSkill,
)
from lampgo.skills.builtin.playback_skills import PlayRecordingSkill
from lampgo.skills.composed import ComposedSkill
from lampgo.skills.executor import SkillExecutor
from lampgo.skills.fsm import StateMachine
from lampgo.skills.loader import (
    LoadReport,
    SkillDefinitionError,
    delete_user_skill,
    load_user_skills,
    save_user_skill,
    user_skills_dir,
    validate_definition,
)
from lampgo.skills.recorder import TeachRecorder
from lampgo.skills.registry import SkillRegistry

logger = structlog.get_logger(__name__)
RECORDING_ALIASES_FILE = "aliases.json"


class LampgoServer:
    """Top-level orchestrator. Owns all components and their lifecycle."""

    def __init__(self, config: LampgoConfig) -> None:
        self.config = config
        self.events = EventBus()
        self.hal = HardwareAbstraction(config.device)
        self.safety = SafetyKernel(config.safety)
        self.motion = MotionRuntime(self.hal, self.safety, config.motion)
        # Backward-compatible LED port resolution:
        # prefer explicit [led].port, then fallback to [device].led_port
        led_port = config.led.port or config.device.led_port
        self.led = LEDController(LEDConfig(port=led_port, baud_rate=config.led.baud_rate))
        self.fsm = StateMachine()
        self.registry = SkillRegistry()
        self.executor = SkillExecutor(self.registry, self.events)
        self.openclaw = OpenClawAdapter(self.registry, self.executor, self.events)
        self.router = IntentRouter()
        self._ipc = IPCServer(self.handle_request, socket_path=config.socket_path)
        self._voice_task: asyncio.Task | None = None
        self._web_gateway = None
        self._state_writer = StateWriter()
        self._recording_alias_cache: tuple[float, dict[str, str]] = (0.0, {})
        self._openclaw_asks: dict[str, asyncio.Future[str]] = {}
        self._openclaw_asks_lock = asyncio.Lock()
        self._tts_tasks: set[asyncio.Task] = set()
        self._record_lock = asyncio.Lock()
        self._record_recorder: TeachRecorder | None = None
        self._record_task: asyncio.Task | None = None
        self._record_started_at: float = 0.0
        self._record_fps: int = 30
        self._record_motion_was_running: bool = False

    def _register_builtin_skills(self) -> None:
        recordings_dir = Path(self.config.recordings_dir)
        self.registry.register(MoveToSkill())
        self.registry.register(ReturnSafeSkill())
        self.registry.register(EStopSkill())
        self.registry.register(PlayRecordingSkill(recordings_dir))
        self.registry.register(SetExpressionSkill())
        self.registry.register(NodSkill())
        self.registry.register(HeadShakeSkill())
        self.registry.register(LookAtSkill())
        self.registry.register(IdleSwaySkill())
        self.registry.register(DanceSkill())

    # ---- user / composed skills (JSON-defined, created by OpenClaw & UI) ----

    def _lampgo_home(self) -> Path:
        """Resolve ~/.lampgo (or $LAMPGO_HOME).  Deferred import to keep the
        server module import-free of personastore at module load time."""
        from lampgo import personastore

        return personastore.lampgo_home()

    def _user_skills_dir(self) -> Path:
        return user_skills_dir(self._lampgo_home())

    def _load_user_skills(self) -> LoadReport:
        """Scan ``~/.lampgo/skills/user/`` and register every valid JSON skill.

        Safe to call repeatedly — it's additive only (existing user skills
        with the same id keep working; this method does not purge stale
        registrations on its own).  For full reload semantics use
        :meth:`_reload_user_skills`.
        """
        report = load_user_skills(self._user_skills_dir(), self.registry)
        if report.errors:
            logger.warning(
                "server.user_skills_partial",
                loaded=report.loaded,
                errors=report.errors,
            )
        else:
            logger.info("server.user_skills_loaded", skills=report.loaded)
        return report

    def _reload_user_skills(self) -> LoadReport:
        """Drop every registered user skill then re-scan disk.

        Used when the user edits a definition out-of-band (e.g. via the
        editor) and wants the daemon to pick up the change without a full
        restart.  Factory skills are untouched.
        """
        current_user_ids = [
            s.skill_id
            for s in self.registry.list_skills()
            if getattr(s, "source", "factory") == "user"
        ]
        for sid in current_user_ids:
            self.registry.unregister(sid)
        return self._load_user_skills()

    def make_context(self) -> SkillContext:
        return SkillContext(
            motion=self.motion,
            led=self.led,
            events=self.events,
            state=self.motion.current_state,
        )

    async def handle_request(self, data: dict[str, Any]) -> dict[str, Any]:
        """Route an IPC request to the appropriate handler."""
        cmd = data.get("cmd", "")

        if cmd == "ping":
            return {"ok": True, "result": "pong"}

        if cmd == "invoke":
            return await self._handle_invoke(data)

        if cmd == "text":
            result = await self._handle_text(data)
            await self._maybe_tts(result, data.get("request_id", ""))
            return result

        if cmd == "audio":
            return await self._handle_audio(data)

        if cmd == "status":
            return self._handle_status()

        if cmd == "skills":
            return self._handle_skills()

        if cmd == "cancel":
            await self.executor.cancel_current()
            return {"ok": True, "result": {"status": "cancelled"}}

        if cmd == "estop":
            self.safety.estop("IPC estop command")
            self.motion.stop_immediate()
            return {"ok": True, "result": {"status": "estopped"}}

        if cmd == "recording_start":
            return await self.start_recording_session(fps=int(data.get("fps", 30) or 30))

        if cmd == "recording_stop":
            return await self.stop_recording_session()

        if cmd == "recording_save":
            return await self.save_recording_session(
                str(data.get("name", "")),
                overwrite=bool(data.get("overwrite", False)),
            )

        if cmd == "recording_discard":
            return await self.discard_recording_session()

        if cmd == "list_cameras":
            return self._handle_list_cameras()

        if cmd == "set_camera":
            return self._handle_set_camera(str(data.get("port", "")))

        if cmd == "list_mics":
            return self._handle_list_mics()

        if cmd == "skills_save":
            return self._handle_skills_save(data)

        if cmd == "skills_delete":
            return self._handle_skills_delete(data)

        if cmd == "skills_reload":
            return self._handle_skills_reload()

        return {"ok": False, "error": f"unknown command: {cmd}"}

    async def openclaw_ask_user(
        self,
        *,
        question: str,
        options: list[str] | None = None,
        request_id: str = "",
        timeout_s: float = 120.0,
    ) -> dict[str, Any]:
        ask_id = f"ask_{uuid.uuid4().hex[:10]}"
        fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        async with self._openclaw_asks_lock:
            self._openclaw_asks[ask_id] = fut

        opts = list(options or [])
        await self.events.publish(OpenClawAskRequested(ask_id=ask_id, question=question, options=opts, request_id=request_id))
        await self.events.publish(ChatMessage(role="assistant", content=question, request_id=request_id))

        try:
            reply = await asyncio.wait_for(fut, timeout=timeout_s)
        except TimeoutError:
            reply = ""
        finally:
            async with self._openclaw_asks_lock:
                self._openclaw_asks.pop(ask_id, None)

        await self.events.publish(OpenClawAskResolved(ask_id=ask_id, reply=reply, request_id=request_id))
        return {"ask_id": ask_id, "reply": reply, "timeout": reply == "", "ts": time.time()}

    async def openclaw_reply_user(self, *, ask_id: str, reply: str, request_id: str = "") -> bool:
        async with self._openclaw_asks_lock:
            fut = self._openclaw_asks.get(ask_id)
        if fut is None or fut.done():
            return False
        fut.set_result(reply)
        await self.events.publish(ChatMessage(role="user", content=reply, request_id=request_id))
        return True

    async def _handle_invoke(self, data: dict) -> dict:
        skill_id = data.get("skill_id", "")
        params = data.get("params", {})

        if self._record_recorder is not None:
            return {
                "ok": False,
                "error": "recording session active; save/discard recording first",
            }

        skill = self.registry.get(skill_id)
        if skill is None:
            return {"ok": False, "error": f"Skill '{skill_id}' not registered"}

        wait = data.get("wait", False)
        ctx = self.make_context()

        if wait:
            result = await self.executor.invoke(skill_id, ctx, **params)
            return {
                "ok": result.status in ("ok", "cancelled"),
                "result": {
                    "invocation_id": result.invocation_id,
                    "status": result.status,
                    "data": result.result,
                    "error": result.error_detail,
                },
            }

        async def _bg():
            await self.executor.invoke(skill_id, ctx, **params)

        asyncio.ensure_future(_bg())
        return {"ok": True, "result": {"status": "accepted", "skill_id": skill_id}}

    async def start_recording_session(self, fps: int = 30) -> dict[str, Any]:
        """Start a teach-recording session in run mode.

        The session samples HAL joint positions at a fixed FPS and buffers frames
        in memory until the caller stops + saves (or discards).
        """
        async with self._record_lock:
            if self.config.no_hw or not self.hal.is_connected:
                return {"ok": False, "error": "hardware not connected"}
            if self._record_recorder is not None:
                return {"ok": False, "error": "recording session already active"}

            fps = max(1, min(120, int(fps or 30)))
            logger.info("server.recording_start_requested", fps=fps)
            await self.executor.cancel_current()
            self._record_motion_was_running = bool(getattr(self.motion, "is_running", False))
            if self._record_motion_was_running:
                self.motion.stop()
            self.hal.disable_torque()

            recordings_dir = Path(self.config.recordings_dir) / "user"
            recordings_dir.mkdir(parents=True, exist_ok=True)
            rec = TeachRecorder(self.hal, recordings_dir, fps=fps)
            rec.start()
            self._record_recorder = rec
            self._record_started_at = time.monotonic()
            self._record_fps = fps
            self._record_task = asyncio.create_task(self._record_loop())
            logger.info("server.recording_started", fps=fps)

            return {
                "ok": True,
                "result": {
                    "status": "recording",
                    "fps": fps,
                    "started_at": self._record_started_at,
                },
            }

    async def stop_recording_session(self) -> dict[str, Any]:
        """Stop sampling but keep buffered frames for save/discard."""
        async with self._record_lock:
            rec = self._record_recorder
            if rec is None:
                return {"ok": False, "error": "no active recording session"}
            rec.stop()
            if self._record_task is not None:
                await self._record_task
                self._record_task = None
            # Product requirement: after recording ends, re-enable torque so the
            # arm holds its current pose and does not slump under gravity.
            self.hal.enable_torque()
            if self._record_motion_was_running:
                self.motion.start()
            self._record_motion_was_running = False
            elapsed = max(0.0, time.monotonic() - self._record_started_at)
            logger.info("server.recording_stopped", frames=rec.frame_count, duration_s=round(elapsed, 3))
            return {
                "ok": True,
                "result": {
                    "status": "stopped",
                    "frames": rec.frame_count,
                    "duration_s": round(elapsed, 3),
                },
            }

    async def save_recording_session(self, name: str, *, overwrite: bool = False) -> dict[str, Any]:
        """Persist buffered recording frames to <recordings_dir>/user/<name>.csv."""
        async with self._record_lock:
            rec = self._record_recorder
            if rec is None:
                return {"ok": False, "error": "no recording session to save"}
            if rec.is_recording:
                return {"ok": False, "error": "recording still active; stop first"}

            name = name.strip()
            if not name or not re.match(r"^[\w\-]+$", name):
                return {"ok": False, "error": "invalid name: use letters/numbers/_/-"}

            target_path = Path(self.config.recordings_dir) / "user" / f"{name}.csv"
            if target_path.exists() and not overwrite:
                return {
                    "ok": False,
                    "error": "recording already exists",
                    "result": {
                        "status": "name_conflict",
                        "name": name,
                        "path": str(target_path),
                        "requires_overwrite": True,
                    },
                }

            path = rec.save(name)
            frames = rec.frame_count
            self._record_recorder = None
            self._record_started_at = 0.0
            self._record_fps = 30
            self._record_motion_was_running = False
            return {
                "ok": True,
                "result": {
                    "status": "saved",
                    "name": name,
                    "path": str(path),
                    "frames": frames,
                    "record_playback_notes": {
                        "record": "samples HAL joint positions only (no style interpolation)",
                        "play": "uses move_to waypoint playback (style-aware planned interpolation)",
                        "safety": "playback follows move_to safety path via validate_frame",
                    },
                },
            }

    async def discard_recording_session(self) -> dict[str, Any]:
        """Discard current recording session (active or stopped)."""
        async with self._record_lock:
            rec = self._record_recorder
            if rec is None:
                return {"ok": False, "error": "no recording session to discard"}
            if rec.is_recording:
                rec.stop()
            if self._record_task is not None:
                await self._record_task
                self._record_task = None
            frames = rec.frame_count
            self._record_recorder = None
            self._record_started_at = 0.0
            self._record_fps = 30
            if self._record_motion_was_running:
                self.motion.start()
            self._record_motion_was_running = False
            return {"ok": True, "result": {"status": "discarded", "frames": frames}}

    async def _record_loop(self) -> None:
        """Background sampling loop for run-mode recording sessions."""
        rec = self._record_recorder
        if rec is None:
            return
        interval = 1.0 / max(1, self._record_fps)
        try:
            while rec.is_recording:
                rec.tick()
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            # Normal shutdown/cancel path.
            pass
        except Exception:
            logger.exception("server.record_loop_failed")

    def _record_status(self) -> dict[str, Any]:
        rec = self._record_recorder
        if rec is None:
            return {"active": False, "has_buffer": False, "frames": 0}
        return {
            "active": rec.is_recording,
            "has_buffer": rec.frame_count > 0,
            "fps": self._record_fps,
            "frames": rec.frame_count,
            "started_at": self._record_started_at,
        }

    def _extract_response_text(self, result: dict) -> str | None:
        r = result.get("result", {})
        return r.get("response") or r.get("chat_response") or None

    async def _maybe_tts(self, result: dict, request_id: str) -> None:
        text = self._extract_response_text(result)
        if text:
            task = asyncio.create_task(self._tts_for_web(text, request_id))
            self._tts_tasks.add(task)
            task.add_done_callback(self._tts_tasks.discard)

    def cancel_pending_tts(self) -> int:
        """Cancel all in-flight TTS synthesis tasks. Returns count cancelled."""
        cancelled = 0
        for t in list(self._tts_tasks):
            if not t.done():
                t.cancel()
                cancelled += 1
        self._tts_tasks.clear()
        if cancelled:
            logger.info("server.tts_cancelled", count=cancelled)
        return cancelled

    async def _handle_text(self, data: dict) -> dict:
        """Route free text through the IntentRouter, then invoke or reply."""
        text = data.get("input", "").strip()
        if not text:
            return {"ok": False, "error": "empty input"}
        if self._record_recorder is not None:
            return {
                "ok": False,
                "error": "recording session active; save/discard recording first",
            }

        request_id = data.get("request_id", "")

        alias = self._resolve_recording_alias(text)
        if alias:
            ctx = self.make_context()
            result = await self.executor.invoke("play_recording", ctx, name=alias)
            return {
                "ok": result.status in ("ok", "cancelled"),
                "result": {
                    "type": "skill",
                    "skill_id": "play_recording",
                    "invocation_id": result.invocation_id,
                    "status": result.status,
                    "data": result.result,
                    "chat_response": f"好的，播放录制动作：{alias}",
                    "source": "recording_alias",
                    "detail": f"recording_alias:{alias}",
                    "matched_keyword": text,
                },
            }

        async def _publish_intent_progress(stage: str, message: str, source: str) -> asyncio.Task | None:
            await self.events.publish(
                IntentProgress(
                    stage=stage,
                    message=message,
                    source=source,
                    request_id=request_id,
                )
            )
            if stage == "llm_narration" and message.strip():
                task = asyncio.create_task(self._tts_for_web(message, request_id))
                self._tts_tasks.add(task)
                task.add_done_callback(self._tts_tasks.discard)
                return task
            return None

        raw_history = data.get("history") or []
        history = raw_history if isinstance(raw_history, list) else []

        intent = self.router.route(text)
        if intent.intent_type == IntentType.COMPLEX and self.router.has_llm_client:
            logger.info(
                "server.text_escalate_to_llm_agent",
                text=text,
                request_id=request_id,
                detail=intent.detail,
                history_len=len(history),
            )
            await _publish_intent_progress("llm_fallback", "关键词未命中，转交 LLM Agent...", "llm")
            agent_result = await self.router.run_agent_loop(
                text,
                execute_tool=lambda tool_name, params, turn_index, tool_index: self._execute_agent_tool(
                    request_id=request_id,
                    tool_name=tool_name,
                    params=params,
                    turn_index=turn_index,
                    tool_index=tool_index,
                ),
                on_progress=_publish_intent_progress,
                joint_state=self.motion.current_state.positions,
                publish_tool_event=lambda phase, **kwargs: self._publish_inline_tool_event(
                    request_id=request_id,
                    phase=phase,
                    **kwargs,
                ),
                history=history,
            )
            if agent_result.intent_type == "complex":
                await self.events.publish(
                    AgentFinished(
                        request_id=request_id,
                        stop_reason=agent_result.stop_reason,
                        tool_call_count=len(agent_result.tool_calls),
                        response=agent_result.response,
                        detail=agent_result.detail,
                    )
                )
                logger.info(
                    "server.text_agent_handoff_to_openclaw",
                    text=text,
                    request_id=request_id,
                    stop_reason=agent_result.stop_reason,
                    detail=agent_result.detail,
                )
                await _publish_intent_progress("openclaw_handoff", "超出当前 tool 能力，转交 OpenClaw...", "openclaw")
                return await self._handoff_to_openclaw(
                    request_id=request_id,
                    text=text,
                    reason=agent_result.detail or "LLM 判定需要 OpenClaw 慢路径",
                    recent_tool_calls=self._serialize_agent_tool_calls(agent_result.tool_calls),
                )
            await self.events.publish(
                AgentFinished(
                    request_id=request_id,
                    stop_reason=agent_result.stop_reason,
                    tool_call_count=len(agent_result.tool_calls),
                    response=agent_result.response,
                    detail=agent_result.detail,
                )
            )
            logger.info(
                "server.text_agent_finished",
                text=text,
                request_id=request_id,
                intent_type=agent_result.intent_type,
                stop_reason=agent_result.stop_reason,
                tool_call_count=len(agent_result.tool_calls),
            )
            return self._format_agent_result(agent_result, text)

        if intent.intent_type == IntentType.COMPLEX:
            await _publish_intent_progress("openclaw_handoff", "关键词未命中且未能本地完成，转交 OpenClaw...", "openclaw")
            return await self._handoff_to_openclaw(
                request_id=request_id,
                text=text,
                reason=intent.detail or "需要 OpenClaw 慢路径处理",
                recent_tool_calls=[],
            )

        logger.info(
            "server.text_intent_resolved",
            text=text,
            request_id=request_id,
            intent_type=intent.intent_type.value,
            skill_id=intent.skill_id,
            source=intent.source,
            detail=intent.detail,
        )

        if intent.intent_type == IntentType.CHAT:
            return {
                "ok": True,
                "result": {
                    "type": "chat",
                    "response": intent.chat_response,
                    "source": intent.source,
                    "detail": intent.detail,
                    "matched_keyword": intent.matched_keyword,
                },
            }

        if intent.intent_type == IntentType.SKILL and intent.skill_id:
            ctx = self.make_context()
            params = intent.params or {}
            result = await self.executor.invoke(intent.skill_id, ctx, **params)
            return {
                "ok": result.status in ("ok", "cancelled"),
                "result": {
                    "type": "skill",
                    "skill_id": intent.skill_id,
                    "invocation_id": result.invocation_id,
                    "status": result.status,
                    "data": result.result,
                    "chat_response": intent.chat_response,
                    "source": intent.source,
                    "detail": intent.detail,
                    "matched_keyword": intent.matched_keyword,
                },
            }

        return {
            "ok": True,
            "result": {
                "type": "complex",
                "response": "This request is too complex for the fast path. Please use OpenClaw.",
                "original_text": text,
                "source": intent.source,
                "detail": intent.detail,
                "matched_keyword": intent.matched_keyword,
            },
        }

    async def _handle_audio(self, data: dict) -> dict:
        """Handle audio input: omni transcribes → text goes through normal _handle_text.

        Two-step approach because mimo-v2-omni cannot reliably do
        function calling + audio understanding simultaneously.
        """
        audio_data = data.get("audio_data", "")
        if not audio_data:
            return {"ok": False, "error": "empty audio_data"}

        request_id = data.get("request_id", "")

        if not self.router.has_llm_client:
            return {"ok": False, "error": "LLM client not configured — cannot process audio"}

        audio_rms = self._measure_audio_rms(audio_data)
        logger.info("server.audio_transcribing", request_id=request_id, audio_b64_len=len(audio_data), rms=f"{audio_rms:.1f}")
        await self.events.publish(IntentRouting(text="[语音输入]", request_id=request_id))
        await self.events.publish(
            IntentProgress(stage="audio_transcribe", message="正在识别语音...", source="llm", request_id=request_id)
        )

        text = await self.router.transcribe_audio(audio_data)
        if not text:
            logger.warning("server.audio_transcribe_empty", request_id=request_id)
            return {"ok": True, "result": {"type": "chat", "response": "抱歉，没有听清您说的话。", "source": "audio"}}

        logger.info("server.audio_transcribed", request_id=request_id, text=text)
        await self.events.publish(
            IntentProgress(stage="audio_transcribed", message=f"听到：{text}", source="llm", request_id=request_id)
        )

        result = await self._handle_text(
            {
                "input": text,
                "request_id": request_id,
                "history": data.get("history") or [],
            }
        )
        await self._maybe_tts(result, request_id)
        return result

    def _resolve_recording_alias(self, text: str) -> str | None:
        path = Path(self.config.recordings_dir) / RECORDING_ALIASES_FILE
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return None

        cached_mtime, cached = self._recording_alias_cache
        if mtime != cached_mtime:
            import json

            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    cached = {str(k).strip(): str(v).strip() for k, v in data.items() if str(k).strip() and str(v).strip()}
                else:
                    cached = {}
            except Exception:
                cached = {}
            self._recording_alias_cache = (mtime, cached)

        return cached.get(text.strip())

    async def _execute_agent_tool(
        self,
        request_id: str,
        tool_name: str,
        params: dict[str, Any],
        turn_index: int,
        tool_index: int,
    ) -> dict[str, Any]:
        await self.events.publish(
            ToolCallPlanned(
                request_id=request_id,
                turn_index=turn_index,
                tool_index=tool_index,
                tool_name=tool_name,
                arguments=params,
            )
        )
        logger.info(
            "server.agent_tool_planned",
            request_id=request_id,
            turn_index=turn_index,
            tool_index=tool_index,
            tool_name=tool_name,
            params=params,
        )
        result = await self.executor.invoke(tool_name, self.make_context(), **params)
        tool_payload = {
            "ok": result.status in ("ok", "cancelled"),
            "status": result.status,
            "result": result.result,
            "error": result.error_detail,
            "invocation_id": result.invocation_id,
        }
        summary = f"{tool_name} -> {result.status}"
        if result.error_detail:
            summary = f"{summary}: {result.error_detail}"
        await self.events.publish(
            ToolCallFinished(
                request_id=request_id,
                turn_index=turn_index,
                tool_index=tool_index,
                tool_name=tool_name,
                status=result.status,
                invocation_id=result.invocation_id,
                summary=summary,
                error=result.error_detail,
            )
        )
        logger.info(
            "server.agent_tool_finished",
            request_id=request_id,
            turn_index=turn_index,
            tool_index=tool_index,
            tool_name=tool_name,
            status=result.status,
            invocation_id=result.invocation_id,
        )
        return tool_payload

    async def _publish_inline_tool_event(
        self,
        *,
        request_id: str,
        phase: str,
        turn_index: int,
        tool_index: int,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        status: str = "",
        error: str | None = None,
    ) -> None:
        """Emit ToolCallPlanned/Finished for inline tools handled inside the LLM client.

        Tools like ``web_search`` / ``capture_image`` short-circuit inside
        :class:`LlmClient` and never touch ``_execute_agent_tool``; without this
        bridge the frontend would never see a tool chip for them.
        """
        if phase == "planned":
            await self.events.publish(
                ToolCallPlanned(
                    request_id=request_id,
                    turn_index=turn_index,
                    tool_index=tool_index,
                    tool_name=tool_name,
                    arguments=arguments or {},
                )
            )
            logger.info(
                "server.agent_inline_tool_planned",
                request_id=request_id,
                turn_index=turn_index,
                tool_index=tool_index,
                tool_name=tool_name,
            )
            return

        if phase == "finished":
            summary = f"{tool_name} -> {status or 'ok'}"
            if error:
                summary = f"{summary}: {error}"
            await self.events.publish(
                ToolCallFinished(
                    request_id=request_id,
                    turn_index=turn_index,
                    tool_index=tool_index,
                    tool_name=tool_name,
                    status=status or "ok",
                    invocation_id=None,
                    summary=summary,
                    error=error,
                )
            )
            logger.info(
                "server.agent_inline_tool_finished",
                request_id=request_id,
                turn_index=turn_index,
                tool_index=tool_index,
                tool_name=tool_name,
                status=status or "ok",
            )

    async def _handoff_to_openclaw(
        self,
        request_id: str,
        text: str,
        reason: str,
        recent_tool_calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        logger.info("server.openclaw_handoff", request_id=request_id, text=text, reason=reason)
        task = await self.openclaw.submit_complex_task(
            {
                "request_id": request_id,
                "user_text": text,
                "reason": reason,
                "available_capabilities": self.openclaw.list_capabilities_dict(),
                "recent_tool_calls": recent_tool_calls,
                "current_state": self._handle_status().get("result", {}),
            }
        )
        return {
            "ok": True,
            "result": {
                "type": "openclaw",
                "response": "已转交 OpenClaw 处理。你可以在任务区查看状态，并在需要时确认 promoted 方案。",
                "source": "openclaw",
                "detail": reason,
                "matched_keyword": None,
                "stop_reason": "openclaw_handoff",
                "openclaw_task": task,
            },
        }

    @staticmethod
    def _measure_audio_rms(audio_b64: str) -> float:
        """Decode base64 WAV and compute RMS to check if audio has content."""
        import base64
        import math
        import struct
        try:
            raw = base64.b64decode(audio_b64)
            if len(raw) < 46:
                return 0.0
            pcm = raw[44:]
            n = len(pcm) // 2
            if n == 0:
                return 0.0
            sum_sq = 0.0
            for i in range(0, len(pcm) - 1, 2):
                sample = struct.unpack_from("<h", pcm, i)[0]
                sum_sq += sample * sample
            return math.sqrt(sum_sq / n)
        except Exception:
            return -1.0

    async def _tts_for_web(self, text: str, request_id: str) -> None:
        """Synthesize TTS and publish audio event for web playback."""
        try:
            from lampgo.voice.tts import synthesize_for_web
            result = await synthesize_for_web(
                text,
                api_key=self.config.llm.api_key,
                voice=self.config.voice.tts_voice,
                provider=self.config.voice.tts_provider,
                model=self.config.voice.tts_model,
            )
            if result:
                audio_b64, fmt = result
                await self.events.publish(TtsAudio(audio=audio_b64, format=fmt, request_id=request_id))
                logger.debug("server.tts_for_web_done", request_id=request_id, format=fmt)
            else:
                logger.warning("server.tts_for_web_empty", request_id=request_id)
        except Exception:
            logger.exception("server.tts_for_web_failed", request_id=request_id)

    @staticmethod
    def _format_agent_result(agent_result, text: str) -> dict[str, Any]:
        tool_calls = [
            {
                "turn_index": call.turn_index,
                "tool_index": call.tool_index,
                "tool_name": call.tool_name,
                "arguments": call.arguments,
                "status": call.status,
                "result": call.result,
                "error": call.error,
                "invocation_id": call.invocation_id,
            }
            for call in agent_result.tool_calls
        ]
        result_type = agent_result.intent_type
        payload = {
            "type": result_type,
            "response": agent_result.response,
            "source": agent_result.source,
            "detail": agent_result.detail,
            "matched_keyword": None,
            "stop_reason": agent_result.stop_reason,
            "tool_calls": tool_calls,
        }
        if result_type == "complex":
            payload["original_text"] = text
        return {"ok": True, "result": payload}

    @staticmethod
    def _serialize_agent_tool_calls(tool_calls) -> list[dict[str, Any]]:
        return [
            {
                "turn_index": call.turn_index,
                "tool_index": call.tool_index,
                "tool_name": call.tool_name,
                "arguments": call.arguments,
                "status": call.status,
                "result": call.result,
                "error": call.error,
                "invocation_id": call.invocation_id,
            }
            for call in tool_calls
        ]

    def _handle_status(self) -> dict:
        positions = self.motion.current_state.positions
        health = "ok" if not self.safety.is_estopped() else "degraded"
        if not self.hal.is_connected:
            health = "disconnected"
        return {
            "ok": True,
            "result": {
                "running_skill": self.executor.current_skill_id,
                "is_busy": self.executor.is_busy,
                "joint_positions": positions,
                "device_health": health,
                "estopped": self.safety.is_estopped(),
                "estop_reason": self.safety.last_estop_reason,
                "recording": self._record_status(),
                "hal_connected": bool(self.hal.is_connected),
                "led_ready": bool(self.led.is_connected),
                "camera_ready": bool(self.config.camera.port.strip()),
            },
        }

    def _handle_list_cameras(self) -> dict:
        """Probe camera indices 0..3 and return availability + names.

        Returns active port based on the in-memory config so the UI can highlight it.
        """
        try:
            import cv2  # noqa: F401
        except ImportError:
            return {
                "ok": True,
                "result": {
                    "cameras": [],
                    "active": self.config.camera.port,
                    "available": False,
                    "reason": "opencv-python not installed",
                },
            }

        try:
            from lampgo.autodetect import _list_camera_names  # type: ignore
            names = _list_camera_names()
        except Exception:
            names = {}

        import os

        import cv2
        cameras: list[dict[str, str]] = []
        for idx in range(4):
            devnull = os.open(os.devnull, os.O_WRONLY)
            old_stderr = os.dup(2)
            os.dup2(devnull, 2)
            try:
                cap = cv2.VideoCapture(idx)
                opened = cap.isOpened()
                cap.release()
            finally:
                os.dup2(old_stderr, 2)
                os.close(devnull)
                os.close(old_stderr)
            if opened:
                cameras.append({"port": str(idx), "name": names.get(idx, "") or f"camera_{idx}"})

        return {
            "ok": True,
            "result": {
                "cameras": cameras,
                "active": self.config.camera.port,
                "available": True,
            },
        }

    def _handle_set_camera(self, port: str) -> dict:
        """Update the active camera port in the in-memory config (runtime switch)."""
        value = (port or "").strip()
        self.config.camera.port = value
        logger.info("camera.port_updated", port=value or "<disabled>")
        return {
            "ok": True,
            "result": {
                "active": self.config.camera.port,
                "camera_ready": bool(value),
            },
        }

    def _handle_list_mics(self) -> dict:
        """Enumerate server-side audio input devices (PyAudio / sounddevice).

        This matches the semantics of ``voice.mic_device`` — a *server-side*
        device index that the Python voice loop opens via ``sounddevice``.
        It is intentionally distinct from the browser-side mic list shown in
        the topbar mic chip popover (which is what the browser streams up
        over WebRTC).
        """
        try:
            import sounddevice as sd  # type: ignore
        except ImportError:
            return {
                "ok": True,
                "result": {
                    "mics": [],
                    "active": self.config.voice.mic_device,
                    "available": False,
                    "reason": "sounddevice not installed",
                },
            }

        mics: list[dict[str, object]] = []
        default_index: int | None = None
        try:
            devices = sd.query_devices()
            try:
                default_index = int(sd.default.device[0])
            except Exception:
                default_index = None
            for i, dev in enumerate(devices):
                if int(dev.get("max_input_channels", 0) or 0) <= 0:
                    continue
                mics.append(
                    {
                        "index": str(i),
                        "name": str(dev.get("name", "") or ""),
                        "is_default": (default_index is not None and i == default_index),
                        "max_input_channels": int(dev.get("max_input_channels", 0) or 0),
                    }
                )
        except Exception as exc:
            return {
                "ok": True,
                "result": {
                    "mics": [],
                    "active": self.config.voice.mic_device,
                    "available": False,
                    "reason": f"enumeration failed: {exc}",
                },
            }

        return {
            "ok": True,
            "result": {
                "mics": mics,
                "active": self.config.voice.mic_device,
                "default": str(default_index) if default_index is not None else "",
                "available": True,
            },
        }

    def _handle_skills(self) -> dict:
        skills = []
        for skill in self.registry.list_skills():
            entry: dict[str, Any] = {
                "skill_id": skill.skill_id,
                "description": skill.description,
                "source": getattr(skill, "source", "factory"),
                "label": getattr(skill, "label", "") or "",
                "parameters": {
                    name: {
                        "type": spec.type,
                        "description": spec.description,
                        "required": spec.required,
                        "default": spec.default,
                    }
                    for name, spec in skill.parameters.items()
                },
            }
            # For user skills we also surface the step plan so the UI can
            # show "这个技能会做什么" without needing a second round-trip.
            if isinstance(skill, ComposedSkill):
                entry["steps"] = [
                    {"skill_id": s["skill_id"], "params": dict(s.get("params") or {})}
                    for s in skill.definition.get("steps", [])
                ]
            skills.append(entry)
        return {"ok": True, "result": {"skills": skills}}

    def _handle_skills_save(self, data: dict) -> dict:
        """Validate + persist + register a user-authored composed skill.

        Called from:
          - OpenClaw (``lampgo_save_skill`` tool)
          - Web UI (``POST /api/skills/save``)

        Body: ``{definition: {...JSON skill spec...}, overwrite?: bool}``

        Update semantics: if the skill already exists as a user skill it is
        rewritten in place.  Factory skill_ids are rejected in validation.
        """
        definition_raw = data.get("definition")
        overwrite = bool(data.get("overwrite", True))

        existing_user_ids = {
            s.skill_id
            for s in self.registry.list_skills()
            if getattr(s, "source", "factory") == "user"
        }
        try:
            normalised = validate_definition(
                definition_raw,
                registry=self.registry,
                existing_user_skills=existing_user_ids,
            )
        except SkillDefinitionError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "result": {"reason": exc.reason},
            }

        skill_id = normalised["skill_id"]
        already_exists = skill_id in existing_user_ids
        if already_exists and not overwrite:
            return {
                "ok": False,
                "error": f"skill '{skill_id}' already exists; set overwrite=true to replace",
                "result": {"reason": "already_exists"},
            }

        path = save_user_skill(self._user_skills_dir(), normalised)

        # Swap the in-memory registration.  Unregister first so the new
        # instance gets a fresh cancellation state if an update happened
        # mid-execution (rare but not impossible).
        if already_exists:
            self.registry.unregister(skill_id)
        self.registry.register(ComposedSkill(normalised, self.registry))
        # Push a fresh skills list to any LLM client so the new tool is
        # exposed on the next turn without a daemon restart.
        self._refresh_llm_skill_tools()

        return {
            "ok": True,
            "result": {
                "skill_id": skill_id,
                "path": str(path),
                "updated": already_exists,
            },
        }

    def _handle_skills_delete(self, data: dict) -> dict:
        """Delete a user-authored composed skill.  Factory skills are refused."""
        skill_id = str(data.get("skill_id", "") or "").strip()
        if not skill_id:
            return {"ok": False, "error": "skill_id required"}

        existing = self.registry.get(skill_id)
        if existing is None:
            return {
                "ok": False,
                "error": f"skill '{skill_id}' not found",
                "result": {"reason": "not_found"},
            }
        if getattr(existing, "source", "factory") != "user":
            return {
                "ok": False,
                "error": f"skill '{skill_id}' is a factory skill and cannot be deleted",
                "result": {"reason": "factory_protected"},
            }

        # Remove from registry + disk.  Order matters: registry first so a
        # late IPC invoke can't race an already-deleted JSON file.
        self.registry.unregister(skill_id)
        file_removed = delete_user_skill(self._user_skills_dir(), skill_id)
        self._refresh_llm_skill_tools()
        return {
            "ok": True,
            "result": {"skill_id": skill_id, "file_removed": file_removed},
        }

    def _handle_skills_reload(self) -> dict:
        """Drop all user skills and re-scan ~/.lampgo/skills/user/ from disk."""
        report = self._reload_user_skills()
        self._refresh_llm_skill_tools()
        return {
            "ok": True,
            "result": {"loaded": report.loaded, "errors": report.errors},
        }

    def _refresh_llm_skill_tools(self) -> None:
        """Rebuild the LLM client's tool specs after a registry mutation.

        The LLMClient caches the tool JSON at construction time; without
        this nudge, newly-saved user skills wouldn't be visible to the
        agent until the next daemon restart — which is the exact pain this
        whole feature is meant to remove.  We reuse the same construction
        path as :meth:`_setup_llm_router` (handles "no API key" gracefully).
        Silently no-ops on any failure — the skill save itself succeeded,
        a stale agent tool list is not worth rolling that back.
        """
        if not getattr(self.config.llm, "api_key", ""):
            return
        try:
            from lampgo.perception.llm_client import LLMClient

            skill_specs = self._handle_skills()["result"]["skills"]
            client = LLMClient(
                self.config.llm, skill_specs, camera_config=self.config.camera
            )
            self.router.set_llm_client(client)
            logger.info(
                "server.llm_router_skills_refreshed",
                count=len(skill_specs),
            )
        except Exception:
            logger.exception("server.refresh_llm_skill_tools_failed")

    async def start(self) -> None:
        logger.info("server.starting")
        if self.config.no_hw:
            logger.info("server.no_hw_mode", msg="Skipping motor and LED connections")
        else:
            # Hardware may fail to open (wrong port, permission, device unplugged).
            # Degrade to no_hw instead of crashing so the Web UI still comes up and
            # the user can fix the port in the settings page.
            try:
                self.hal.connect()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "server.hal_connect_failed_degrading_to_no_hw",
                    motor_port=self.config.device.motor_port,
                    error=str(exc),
                )
                print(
                    f"[warn] failed to open motor_port={self.config.device.motor_port!r}: {exc}\n"
                    "       falling back to --no-hw; fix the port in the Web UI (硬件 tab) "
                    "or via `lampgo onboard`, then restart.",
                    flush=True,
                )
                self.config.no_hw = True
                self.config.home_on_start = False
            else:
                try:
                    self.led.connect()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("server.led_connect_failed", error=str(exc))
                self.motion.start()
                home = self.hal.get_calibration_home()
                if home is not None:
                    from lampgo.skills.builtin.motion_skills import set_calibration_home
                    set_calibration_home(home)

        self._register_builtin_skills()
        # Load any user-authored / OpenClaw-authored composed skills from
        # ~/.lampgo/skills/user/ AFTER factory skills so their step targets
        # resolve during validation.
        self._load_user_skills()
        if self.config.home_on_start:
            await self._home_on_start()
        await self._ipc.start()
        self._setup_llm_router()
        self._state_writer.start(get_state=self._get_minimal_state)
        logger.info(
            "server.ready",
            skills=self.registry.list_ids(),
            motor_port="(disabled)" if self.config.no_hw else self.config.device.motor_port,
            socket=self.config.socket_path,
        )

    def _get_minimal_state(self) -> MinimalState:
        camera_connected = bool(self.config.camera.port.strip())
        mic_active = bool(self.config.voice_enabled)
        return MinimalState(
            status="busy" if self.executor.is_busy else "idle",
            is_busy=self.executor.is_busy,
            running_skill=self.executor.current_skill_id,
            estopped=self.safety.is_estopped(),
            camera_connected=camera_connected,
            mic_active=mic_active,
        )

    async def _home_on_start(self) -> None:
        """Slowly return to the fixed safe position on startup."""
        from lampgo.skills.builtin.motion_skills import STARTUP_HOME_VELOCITY, get_safe_position

        home = get_safe_position()

        logger.info("server.homing", velocity=STARTUP_HOME_VELOCITY, target=home)
        try:
            ctx = self.make_context()
            result = await self.executor.invoke("move_to", ctx, velocity=STARTUP_HOME_VELOCITY, **home)
            if result.status == "ok":
                logger.info("server.homed")
            else:
                logger.warning("server.homing_failed", status=result.status, error=result.error_detail)
        except Exception:
            logger.exception("server.homing_error")

    def _setup_llm_router(self) -> None:
        """Wire the LLM client into the IntentRouter if an API key is configured."""
        if not self.config.llm.api_key:
            logger.info("server.llm_router_disabled (no API key)")
            return
        try:
            from lampgo.perception.llm_client import LLMClient

            skill_specs = self._handle_skills()["result"]["skills"]
            client = LLMClient(self.config.llm, skill_specs, camera_config=self.config.camera)
            self.router.set_llm_client(client)
            logger.info(
                "server.llm_router_enabled",
                model=self.config.llm.fast_model,
                camera_port=self.config.camera.port or None,
            )
        except Exception:
            logger.exception("server.llm_router_setup_failed")

    def reload_llm_client(self) -> bool:
        """Rebuild the LLMClient from the current config (called after live config edits)."""
        try:
            from lampgo.perception.llm_client import LLMClient
        except Exception:
            logger.exception("server.reload_llm_client_import_failed")
            return False
        if not self.config.llm.api_key:
            logger.info("server.reload_llm_client_skipped (no API key)")
            try:
                self.router.set_llm_client(None)
            except Exception:
                pass
            return False
        try:
            skill_specs = self._handle_skills()["result"]["skills"]
            client = LLMClient(self.config.llm, skill_specs, camera_config=self.config.camera)
            self.router.set_llm_client(client)
            logger.info(
                "server.llm_router_reloaded",
                provider=self.config.llm.provider,
                model=self.config.llm.model,
                fast_model=self.config.llm.fast_model,
            )
            return True
        except Exception:
            logger.exception("server.reload_llm_client_failed")
            return False

    async def _run_blocking_shutdown_step(self, name: str, fn, timeout_s: float = 2.0) -> None:
        """Run a potentially blocking shutdown step with timeout guard."""
        try:
            await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout_s)
        except TimeoutError:
            logger.error("server.shutdown_step_timeout", step=name, timeout_s=timeout_s)
        except Exception:
            logger.exception("server.shutdown_step_failed", step=name)

    async def shutdown(self) -> None:
        logger.info("server.shutting_down")
        async with self._record_lock:
            if self._record_recorder is not None and self._record_recorder.is_recording:
                self._record_recorder.stop()
            if self._record_task is not None and not self._record_task.done():
                self._record_task.cancel()
                try:
                    await self._record_task
                except asyncio.CancelledError:
                    pass
            self._record_task = None
            self._record_recorder = None
        await self._state_writer.stop()
        if self._voice_task is not None:
            self._voice_task.cancel()
            try:
                await self._voice_task
            except asyncio.CancelledError:
                pass
        await self._ipc.stop()
        if not self.config.no_hw:
            self.motion.stop()
            await self._run_blocking_shutdown_step("led.off", self.led.off)
            await self._run_blocking_shutdown_step("led.disconnect", self.led.disconnect)
            await self._run_blocking_shutdown_step("hal.disconnect", self.hal.disconnect, timeout_s=3.0)
        logger.info("server.stopped")

    async def run_forever(self) -> None:
        await self.start()
        if self.config.voice_enabled:
            self._start_voice_loop()
        if self.config.web_enabled:
            await self._start_web_gateway()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        logger.info("server.running (Ctrl+C to stop)")
        await stop.wait()
        await self.shutdown()

    async def _start_web_gateway(self) -> None:
        try:
            import uvicorn

            from lampgo.web.gateway import WebGateway

            gw = WebGateway(self, self.config.web)
            self._web_gateway = gw

            uvi_config = uvicorn.Config(
                gw.app,
                host=self.config.web.host,
                port=self.config.web.port,
                log_level="warning",
            )
            uvi_server = uvicorn.Server(uvi_config)
            self._web_serve_task = asyncio.create_task(uvi_server.serve())
            local_url = f"http://localhost:{self.config.web.port}"
            logger.info(
                "server.web_started",
                url=local_url,
            )
            self._print_connection_banner(local_url)
        except ImportError:
            logger.error("server.web_missing_deps (pip install starlette uvicorn websockets)")
        except Exception:
            logger.exception("server.web_start_failed")

    @staticmethod
    def _print_connection_banner(local_url: str) -> None:
        """Print a clear banner so users know how to wire up OpenClaw when port changes."""
        try:
            from lampgo.bridge.openclaw_installer import detect_openclaw_integration

            status = detect_openclaw_integration()
            overall = status.overall
            def _mark(ok: bool) -> str:
                return "✓" if ok else "✗"
            integration_lines = [
                f"openclaw CLI     : {_mark(status.binary.ok)} {status.binary.detail}",
                f"lampgo plugin    : {_mark(status.plugin.ok)} {'已安装' if status.plugin.ok else '未安装（运行 `lampgo install-openclaw --yes` 一键安装）'}",
                f"lampgo skill     : {_mark(status.skill.ok)} {'已注册' if status.skill.ok else '未注册（关键词触发不可用）'}",
                f"plugin 启用      : {_mark(status.trusted.ok)} {'已启用' if status.trusted.ok else '未启用（OpenClaw 会拒绝加载）'}",
                f"gateway 在线     : {_mark(status.gateway.ok)} {status.gateway.detail}",
            ]
            overall_line = f"集成状态         : {overall}"
        except Exception:
            integration_lines = ["openclaw 集成检测失败（忽略）"]
            overall_line = ""

        lines = [
            "",
            "──────── lampgo ready ────────",
            f"Web UI           : {local_url}",
            f"OpenClaw plugin  : set lampgoApiBase = {local_url}",
            f"Env override     : export LAMPGO_API_BASE={local_url}",
            *integration_lines,
        ]
        if overall_line:
            lines.append(overall_line)
        lines += [
            "一键修复集成     : uv run lampgo install-openclaw --yes",
            "──────────────────────────────",
            "",
        ]
        print("\n".join(lines), flush=True)

    def _start_voice_loop(self) -> None:
        try:
            from lampgo.voice.loop import VoiceLoop

            self._voice = VoiceLoop(self)
            self._voice_task = asyncio.create_task(self._voice.run())
            logger.info("server.voice_loop_started")
        except Exception:
            logger.exception("server.voice_loop_failed")


async def run_server(config: LampgoConfig) -> None:
    server = LampgoServer(config)
    await server.run_forever()
