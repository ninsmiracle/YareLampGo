"""Main entry point — creates all components and runs the asyncio loop.

The server owns:
  - Hardware (HAL, LED, Motion, Safety)
  - Skill system (Registry, Executor, FSM)
  - IPC server (Unix socket for CLI / OpenClaw / scripts)
  - IntentRouter (keyword + optional fast LLM)
"""

from __future__ import annotations

import asyncio
import random
import re
import signal
import time
import uuid
from collections import deque
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
    SkillStarted,
    ToolCallFinished,
    ToolCallPlanned,
    TtsAudio,
)
from lampgo.core.hal import HardwareAbstraction
from lampgo.core.led import LEDController
from lampgo.core.motion import MotionRuntime
from lampgo.core.safety import SafetyKernel
from lampgo.core.virtual_motion import VirtualMotionRuntime
from lampgo.device import Esp32DeviceManager
from lampgo.ipc import IPCServer
from lampgo.perception.router import IntentRouter, IntentType
from lampgo.recordings import build_recording_actions_prompt, write_recording_description
from lampgo.skills.base import SkillContext
from lampgo.skills.builtin.expression_skills import SetExpressionSkill
from lampgo.skills.builtin.motion_skills import EStopSkill, MoveToSkill, ReturnSafeSkill
from lampgo.skills.builtin.music_skills import DanceToMusicSkill
from lampgo.skills.builtin.parametric_skills import (
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
from lampgo.voice.echo_filter import likely_recent_tts_echo, remember_tts_text
from lampgo.voice.stt import VolcengineASR, build_stt

logger = structlog.get_logger(__name__)
RECORDING_ALIASES_FILE = "aliases.json"


def _log_safe(value: Any, *, limit: int = 200) -> str:
    text = str(value or "")
    text = re.sub(r"[\r\n\t\x00-\x1f\x7f]+", " ", text)
    return text[:limit]


class LampgoServer:
    """Top-level orchestrator. Owns all components and their lifecycle."""

    def __init__(self, config: LampgoConfig) -> None:
        self.config = config
        self.events = EventBus()
        self.hal = HardwareAbstraction(config.device)
        self.safety = SafetyKernel(config.safety)
        self.motion = MotionRuntime(self.hal, self.safety, config.motion)
        self.esp32 = Esp32DeviceManager(config.device_esp32)
        # LED expressions now go through the paired ESP32 Wi-Fi endpoint. Keep
        # the old serial config fields readable for legacy files, but do not
        # open a local LED serial port from the web runtime.
        self.led = LEDController(LEDConfig(port="", baud_rate=config.led.baud_rate), esp32_manager=self.esp32)
        self.fsm = StateMachine()
        self.registry = SkillRegistry()
        self.executor = SkillExecutor(self.registry, self.events)
        self.openclaw = OpenClawAdapter(self.registry, self.executor, self.events)
        self.router = IntentRouter()
        self._stt: VolcengineASR = build_stt(config)
        self._ipc = IPCServer(self.handle_request, socket_path=config.socket_path)
        self._voice_task: asyncio.Task | None = None
        self._voice = None
        self._wake_loop = None
        self._agent_sdk = None
        self._voice_reload_handle: asyncio.TimerHandle | None = None
        self._web_gateway = None
        self._state_writer = StateWriter()
        self._recording_alias_cache: tuple[float, dict[str, str]] = (0.0, {})
        self._openclaw_asks: dict[str, asyncio.Future[str]] = {}
        self._openclaw_asks_lock = asyncio.Lock()
        self._tts_tasks: set[asyncio.Task] = set()
        self._tts_lock = asyncio.Lock()
        self._cancelled_request_ids: set[str] = set()
        self._record_lock = asyncio.Lock()
        self._record_recorder: TeachRecorder | None = None
        self._record_task: asyncio.Task | None = None
        self._record_started_at: float = 0.0
        self._record_fps: int = 30
        self._record_motion_was_running: bool = False
        self._motor_reload_lock = asyncio.Lock()
        self._idle_sway_task: asyncio.Task | None = None
        self._last_foreground_activity_at = time.monotonic()
        self._next_idle_sway_at = 0.0
        self._auto_idle_sway_invoking = False
        self._started = False
        self.events.subscribe(SkillStarted, self._on_skill_started)

    def _use_virtual_motion(self) -> None:
        """Switch motion to the no-hardware in-memory runtime."""
        if isinstance(self.motion, VirtualMotionRuntime):
            if not self.motion.is_running:
                self.motion.start()
            return
        if self.motion.is_running:
            self.motion.stop()
        self.motion = VirtualMotionRuntime(self.config.motion)
        self.motion.start()

    def _resume_motion_after_recording(self) -> None:
        if self.config.no_hw or not self.hal.is_connected:
            return
        if not self.motion.is_running:
            self.motion.start()

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
        self.registry.register(DanceToMusicSkill())

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

    async def _on_skill_started(self, event: SkillStarted) -> None:
        if event.skill_id == "idle_sway" and self._auto_idle_sway_invoking:
            return
        self._mark_foreground_activity()

    def _mark_foreground_activity(self) -> None:
        now = time.monotonic()
        self._last_foreground_activity_at = now
        self._schedule_next_idle_sway(now)

    def _idle_sway_delay_s(self) -> float:
        cfg = self.config.motion
        base = max(float(getattr(cfg, "idle_sway_interval_s", 30.0)), 1.0)
        jitter = max(float(getattr(cfg, "idle_sway_interval_jitter_s", 0.0)), 0.0)
        if jitter > 0:
            base += random.uniform(-jitter, jitter)
        return max(1.0, base)

    def _schedule_next_idle_sway(self, now: float | None = None) -> None:
        self._next_idle_sway_at = (now if now is not None else time.monotonic()) + self._idle_sway_delay_s()

    def _start_idle_sway_scheduler(self) -> None:
        if self._idle_sway_task is not None and not self._idle_sway_task.done():
            return
        self._mark_foreground_activity()
        self._idle_sway_task = asyncio.create_task(self._idle_sway_scheduler_loop())

    async def _stop_idle_sway_scheduler(self) -> None:
        task = self._idle_sway_task
        self._idle_sway_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _idle_sway_scheduler_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            cfg = self.config.motion
            if not getattr(cfg, "idle_sway_enabled", True):
                self._next_idle_sway_at = 0.0
                continue
            if not self.motion.is_running or self.safety.is_estopped() or self._record_recorder is not None:
                self._mark_foreground_activity()
                continue
            if self.executor.current_skill_id:
                continue

            now = time.monotonic()
            idle_after_s = max(float(getattr(cfg, "idle_sway_idle_after_s", 600.0)), 0.0)
            if now - self._last_foreground_activity_at < idle_after_s:
                continue
            if self._next_idle_sway_at <= 0.0:
                self._schedule_next_idle_sway(now)
                continue
            if now < self._next_idle_sway_at:
                continue

            params = {
                "amplitude": float(getattr(cfg, "idle_sway_amplitude", 6.0)),
                "period": float(getattr(cfg, "idle_sway_period_s", 4.5)),
                "duration": float(getattr(cfg, "idle_sway_duration_s", 8.0)),
            }
            logger.info("server.idle_sway_auto_trigger", params=params)
            self._auto_idle_sway_invoking = True
            try:
                result = await self.executor.invoke("idle_sway", self.make_context(), **params)
                if result.status != "ok":
                    logger.warning(
                        "server.idle_sway_auto_failed",
                        status=result.status,
                        error=result.error_detail,
                    )
            except Exception:
                logger.exception("server.idle_sway_auto_error")
            finally:
                self._auto_idle_sway_invoking = False
                self._schedule_next_idle_sway(time.monotonic())

    async def handle_request(self, data: dict[str, Any]) -> dict[str, Any]:
        """Route an IPC request to the appropriate handler."""
        cmd = data.get("cmd", "")

        if cmd == "ping":
            return {"ok": True, "result": "pong"}

        if cmd == "invoke":
            return await self._handle_invoke(data)

        if cmd == "text":
            request_id = str(data.get("request_id", ""))
            self.clear_request_cancelled(request_id)
            try:
                result = await self._handle_text(data)
                if not data.get("call_mode"):
                    await self._maybe_tts(result, request_id)
                return result
            finally:
                self.clear_request_cancelled(request_id)

        if cmd == "audio":
            request_id = str(data.get("request_id", ""))
            self.clear_request_cancelled(request_id)
            try:
                return await self._handle_audio(data)
            finally:
                self.clear_request_cancelled(request_id)

        if cmd == "status":
            return self._handle_status()

        if cmd == "skills":
            return self._handle_skills()

        if cmd == "cancel":
            cancelled = await self.stop_all_interactions(request_id=str(data.get("request_id", "")))
            return {"ok": True, "result": {"status": "cancelled", "cancelled": cancelled}}

        if cmd == "estop":
            self.safety.estop("IPC estop command")
            self.motion.stop_immediate()
            return {"ok": True, "result": {"status": "estopped"}}

        if cmd == "start_conversation":
            if self._wake_loop:
                if self._wake_loop.conversation_state.value == "idle":
                    backfill = deque(self._wake_loop._ring_buffer)
                    self._wake_loop._ring_buffer.clear()
                    ok = await self._wake_loop.bridge.start_conversation(backfill=backfill)
                    if ok:
                        return {"ok": True, "result": {"status": "conversation_started"}}
                    return {"ok": False, "error": "failed to join LiveKit room"}
                return {"ok": False, "error": f"conversation already {self._wake_loop.conversation_state.value}"}
            return {"ok": False, "error": "wake loop not active"}

        if cmd == "stop_conversation":
            if self._wake_loop:
                await self._wake_loop.end_conversation()
                return {"ok": True, "result": {"status": "conversation_ended"}}
            return {"ok": False, "error": "wake loop not active"}

        if cmd == "recording_start":
            return await self.start_recording_session(fps=int(data.get("fps", 30) or 30))

        if cmd == "recording_stop":
            return await self.stop_recording_session()

        if cmd == "recording_save":
            return await self.save_recording_session(
                str(data.get("name", "")),
                overwrite=bool(data.get("overwrite", False)),
                description=str(data.get("description", "") or data.get("prompt", "")),
                expression=str(data.get("expression", "")),
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

        # Teach recording owns the motion stack, but LED expressions are safe
        # to change while a recording is active or waiting to be saved.
        if self._record_recorder is not None and skill_id != "set_expression":
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
            self._resume_motion_after_recording()
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

    async def save_recording_session(
        self,
        name: str,
        *,
        overwrite: bool = False,
        description: str = "",
        expression: str = "",
    ) -> dict[str, Any]:
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
            write_recording_description(Path(path), description, expression)
            frames = rec.frame_count
            self._record_recorder = None
            self._record_started_at = 0.0
            self._record_fps = 30
            self._record_motion_was_running = False
            self._refresh_llm_skill_tools()
            return {
                "ok": True,
                "result": {
                    "status": "saved",
                    "name": name,
                    "path": str(path),
                    "description": description,
                    "expression": expression,
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
            self.hal.enable_torque()
            frames = rec.frame_count
            self._record_recorder = None
            self._record_started_at = 0.0
            self._record_fps = 30
            self._resume_motion_after_recording()
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
        payload = result.get("result", {}) if isinstance(result, dict) else {}
        if isinstance(payload, dict) and payload.get("suppress_final_tts"):
            logger.debug("server.tts_final_suppressed", request_id=request_id)
            return
        text = self._extract_response_text(result)
        if text:
            remember_tts_text(self, text)
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

    def cancel_request(self, request_id: str) -> None:
        request_id = str(request_id or "").strip()
        if request_id:
            self._cancelled_request_ids.add(request_id)
            logger.info("server.request_cancelled", request_id=request_id)

    def clear_request_cancelled(self, request_id: str) -> None:
        request_id = str(request_id or "").strip()
        if request_id:
            self._cancelled_request_ids.discard(request_id)

    def is_request_cancelled(self, request_id: str) -> bool:
        request_id = str(request_id or "").strip()
        return bool(request_id and request_id in self._cancelled_request_ids)

    async def stop_all_interactions(self, *, request_id: str = "") -> dict[str, int]:
        """Cancel active conversation work: LLM turn, TTS, and any running tool."""
        active_task: asyncio.Task | None = getattr(self, "_llm_active_task", None)
        active_request_id = str(getattr(self, "_llm_active_request_id", "") or "")
        if request_id:
            self.cancel_request(request_id)
        if active_request_id:
            self.cancel_request(active_request_id)
        cancelled_llm = 0
        if active_task is not None and not active_task.done():
            active_task.cancel()
            cancelled_llm = 1
        cancelled_tts = self.cancel_pending_tts()
        await self.executor.cancel_current()
        logger.info(
            "server.stop_all_interactions",
            request_id=request_id,
            active_request_id=active_request_id,
            cancelled_llm=cancelled_llm,
            cancelled_tts=cancelled_tts,
        )
        return {"llm": cancelled_llm, "tts": cancelled_tts}

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
        enable_thinking = (
            bool(data.get("enable_thinking"))
            if "enable_thinking" in data
            else bool(self.config.llm.enable_thinking)
        )
        raw_history = data.get("history") or []
        history = raw_history if isinstance(raw_history, list) else []
        call_mode = (
            bool(data.get("call_mode"))
            or
            self._wake_loop is not None
            and self._wake_loop.conversation_state.value in ("joining", "active")
        )
        voice_input = bool(data.get("voice_input")) or call_mode

        if voice_input and not bool(data.get("echo_checked")):
            is_echo, echo_detail = likely_recent_tts_echo(self, text)
            if is_echo:
                logger.info(
                    "server.echo_text_dropped",
                    text=_log_safe(text, limit=80),
                    request_id=request_id,
                    **echo_detail,
                )
                return {
                    "ok": True,
                    "result": {
                        "type": "chat",
                        "response": "",
                        "source": "echo_filter",
                        "detail": "dropped likely self-echo ASR text",
                        "matched_keyword": None,
                        "echo_filtered": True,
                    },
                }
            logger.info(
                "server.echo_text_kept",
                text=_log_safe(text, limit=80),
                request_id=request_id,
                **echo_detail,
            )

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
            if stage == "llm_narration" and message.strip():
                remember_tts_text(self, message)
            await self.events.publish(
                IntentProgress(
                    stage=stage,
                    message=message,
                    source=source,
                    request_id=request_id,
                )
            )
            if stage == "llm_narration" and message.strip() and not call_mode:
                task = asyncio.create_task(self._tts_for_web(message, request_id))
                self._tts_tasks.add(task)
                task.add_done_callback(self._tts_tasks.discard)
                return task
            return None

        intent = self.router.route(text)
        if intent.intent_type == IntentType.COMPLEX and self.router.has_llm_client:
            logger.info(
                "server.text_escalate_to_llm_agent",
                text=_log_safe(text),
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
                call_mode=call_mode,
                enable_thinking=enable_thinking,
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
                    text=_log_safe(text),
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
                text=_log_safe(text),
                request_id=request_id,
                intent_type=agent_result.intent_type,
                stop_reason=agent_result.stop_reason,
                tool_call_count=len(agent_result.tool_calls),
            )
            result = self._format_agent_result(agent_result, text)
            if agent_result.end_conversation:
                self._schedule_end_conversation(
                    request_id=request_id,
                    response_text=agent_result.response or "",
                )
            return result

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
            text=_log_safe(text),
            request_id=request_id,
            intent_type=intent.intent_type.value,
            skill_id=intent.skill_id,
            source=intent.source,
            detail=intent.detail,
        )

        if intent.intent_type == IntentType.CHAT:
            end_conversation = bool(getattr(intent, "end_conversation", False))
            response_text = intent.chat_response or ""
            if end_conversation and call_mode:
                self._schedule_end_conversation(
                    request_id=request_id,
                    response_text=response_text,
                )
            return {
                "ok": True,
                "result": {
                    "type": "chat",
                    "response": response_text,
                    "source": intent.source,
                    "detail": intent.detail,
                    "matched_keyword": intent.matched_keyword,
                    "end_conversation": end_conversation,
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
        """Handle audio input: STT transcribes → text goes through normal _handle_text.

        Uses the standalone STT module (stt_provider from config.voice),
        independent of the LLM client type.
        """
        audio_data = data.get("audio_data", "")
        if not audio_data:
            return {"ok": False, "error": "empty audio_data"}

        request_id = data.get("request_id", "")

        audio_rms = self._measure_audio_rms(audio_data)
        logger.info("server.audio_transcribing", request_id=request_id, audio_b64_len=len(audio_data), rms=f"{audio_rms:.1f}")
        await self.events.publish(IntentRouting(text="[语音输入]", request_id=request_id))
        await self.events.publish(
            IntentProgress(stage="audio_transcribe", message="正在识别语音...", source="stt", request_id=request_id)
        )

        text = await self._stt.transcribe_wav_b64(audio_data)
        if not text:
            logger.warning("server.audio_transcribe_empty", request_id=request_id)
            return {"ok": True, "result": {"type": "chat", "response": "抱歉，没有听清您说的话。", "source": "audio"}}

        logger.info("server.audio_transcribed", request_id=request_id, text=_log_safe(text))
        is_echo, echo_detail = likely_recent_tts_echo(self, text)
        if is_echo:
            logger.info(
                "server.audio_echo_text_dropped",
                text=_log_safe(text, limit=80),
                request_id=request_id,
                **echo_detail,
            )
            return {
                "ok": True,
                "result": {
                    "type": "chat",
                    "response": "",
                    "source": "echo_filter",
                    "detail": "dropped likely self-echo ASR text",
                    "matched_keyword": None,
                    "echo_filtered": True,
                },
            }
        logger.info(
            "server.audio_echo_text_kept",
            text=_log_safe(text, limit=80),
            request_id=request_id,
            **echo_detail,
        )
        await self.events.publish(
            IntentProgress(stage="audio_transcribed", message=f"听到：{text}", source="llm", request_id=request_id)
        )

        result = await self._handle_text(
            {
                "input": text,
                "request_id": request_id,
                "history": data.get("history") or [],
                "voice_input": True,
                "echo_checked": True,
                "enable_thinking": (
                    bool(data.get("enable_thinking"))
                    if "enable_thinking" in data
                    else bool(self.config.llm.enable_thinking)
                ),
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
        current_task = asyncio.current_task()
        if self.is_request_cancelled(request_id) or (current_task is not None and current_task.cancelling()):
            logger.info(
                "server.agent_tool_skipped_cancelled_request",
                request_id=request_id,
                turn_index=turn_index,
                tool_index=tool_index,
                tool_name=tool_name,
            )
            raise asyncio.CancelledError
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
        if self.is_request_cancelled(request_id) or (current_task is not None and current_task.cancelling()):
            raise asyncio.CancelledError
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
        logger.info("server.openclaw_handoff", request_id=request_id, text=_log_safe(text), reason=_log_safe(reason))
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
        """Synthesize TTS and publish audio event for web playback.

        During an active LiveKit voice call the Agent SDK plays Volcengine TTS
        directly into the room, so the local web TTS stream would only be
        wasted bandwidth (and may double-up with the call audio if the Web UI
        is also open). Skip the synthesis but keep the call signature so agent
        loop timing (e.g. ``await asyncio.sleep(1.5)`` after ``say``) is
        preserved.
        """
        if self._wake_loop is not None:
            state = self._wake_loop.conversation_state.value
            if state in ("joining", "active"):
                logger.debug(
                    "server.tts_for_web_skipped_in_call",
                    request_id=request_id,
                    conversation_state=state,
                )
                return

        async with self._tts_lock:
            await self._tts_for_web_locked(text, request_id)

    async def _tts_for_web_locked(self, text: str, request_id: str) -> None:
        try:
            from lampgo.voice.tts import iter_synthesize_for_web

            published = False
            async for audio_b64, fmt, sample_rate in iter_synthesize_for_web(
                text,
                app_id=self.config.voice.volcengine_app_id,
                access_token=self.config.voice.volcengine_access_token,
                voice=self.config.voice.tts_voice,
                provider=self.config.voice.tts_provider,
                model=self.config.voice.tts_model,
            ):
                published = True
                await self.events.publish(
                    TtsAudio(audio=audio_b64, format=fmt, sample_rate=sample_rate, request_id=request_id)
                )
            if published:
                logger.debug("server.tts_for_web_done", request_id=request_id)
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
            "spoken_texts": list(getattr(agent_result, "spoken_texts", []) or []),
            "suppress_final_tts": bool(getattr(agent_result, "suppress_final_tts", False)),
        }
        if getattr(agent_result, "end_conversation", False):
            payload["end_conversation"] = True
        if result_type == "complex":
            payload["original_text"] = text
        return {"ok": True, "result": payload}

    def _schedule_end_conversation(self, *, request_id: str, response_text: str = "") -> None:
        """End an active LiveKit conversation after goodbye TTS has played.

        The LiveKit bridge can observe the SDK's remote TTS audio track and the
        local jitter buffer, so prefer waiting for real playout completion over
        guessing from text length. A timeout still closes the call if audio never
        arrives or the stream gets stuck.
        """
        logger.info(
            "server.conversation_end_waiting_for_tts",
            request_id=request_id,
            text_len=len(response_text or ""),
        )

        async def _end_later() -> None:
            try:
                if self._wake_loop is None:
                    return
                if self._wake_loop.conversation_state.value not in ("joining", "active"):
                    return
                # Wait for the goodbye TTS to actually play out before leaving
                # the LiveKit room. Generous bounds: TTS synthesis + buffering
                # + network can easily take 3-5s before the first frame, and a
                # short farewell still takes 4-6s of audio to play.
                await self._wake_loop.bridge.wait_for_remote_playout(
                    first_frame_timeout_s=10.0,
                    idle_s=1.2,
                    max_wait_s=25.0,
                )
                if self._wake_loop is None:
                    return
                if self._wake_loop.conversation_state.value not in ("joining", "active"):
                    return
                logger.info("server.conversation_end_scheduled", request_id=request_id)
                await self._wake_loop.end_conversation()
            except asyncio.CancelledError:
                logger.info("server.conversation_end_cancelled", request_id=request_id)
                raise
            except Exception:
                logger.debug("server.conversation_end_schedule_failed", request_id=request_id, exc_info=True)
            finally:
                # Clear our slot only if it still points at us — a follow-up
                # end_conversation could have replaced it already.
                if getattr(self, "_pending_hangup_task", None) is asyncio.current_task():
                    self._pending_hangup_task = None  # type: ignore[attr-defined]
                    self._pending_hangup_request_id = ""  # type: ignore[attr-defined]

        # If a previous goodbye was already pending (rare: two end_conversation
        # in close succession), cancel it before scheduling the new one.
        prev_task: asyncio.Task | None = getattr(self, "_pending_hangup_task", None)
        if prev_task is not None and not prev_task.done():
            prev_task.cancel()

        task = asyncio.create_task(_end_later())
        self._pending_hangup_task = task  # type: ignore[attr-defined]
        self._pending_hangup_request_id = request_id  # type: ignore[attr-defined]

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
        virtual = bool(getattr(self.motion, "is_virtual", False))
        if not self.hal.is_connected and not virtual:
            health = "disconnected"
        return {
            "ok": True,
            "result": {
                "running_skill": self.executor.current_skill_id,
                "is_busy": self.executor.is_busy,
                "joint_positions": positions,
                "device_health": health,
                "no_hw": bool(self.config.no_hw),
                "virtual_motion": virtual,
                "estopped": self.safety.is_estopped(),
                "estop_reason": self.safety.last_estop_reason,
                "recording": self._record_status(),
                "hal_connected": bool(self.hal.is_connected),
                "led_ready": bool(self.led.is_connected),
                "camera_ready": bool(self.config.camera.port.strip()) or bool(self.config.device_esp32.enabled),
                "conversation_state": self._wake_loop.conversation_state.value if self._wake_loop else None,
            },
        }

    def _handle_list_cameras(self) -> dict:
        """Probe camera indices 0..3 and return availability + names.

        Returns active port based on the in-memory config so the UI can highlight it.
        """
        try:
            import cv2  # noqa: F401
        except ImportError:
            active = "esp32" if self.config.device_esp32.enabled else self.config.camera.port
            return {
                "ok": True,
                "result": {
                    "cameras": [],
                    "active": active,
                    "available": False,
                    "reason": "opencv-python not installed",
                    "esp32": self._esp32_camera_info(),
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

        esp32_info = self._esp32_camera_info()
        active = "esp32" if self.config.device_esp32.enabled else self.config.camera.port

        return {
            "ok": True,
            "result": {
                "cameras": cameras,
                "active": active,
                "available": True,
                "esp32": esp32_info,
            },
        }

    def _esp32_camera_info(self) -> dict:
        """Build ESP32 camera metadata for the camera popover."""
        cfg = self.config.device_esp32
        if not cfg.enabled:
            return {"enabled": False}
        online = self.esp32.is_online() if self.esp32 else False
        status = self.esp32.get_status() if self.esp32 else {}
        device = status.get("device") if isinstance(status, dict) else None
        blocked = int(status.get("blocked_devices_count") or 0) if isinstance(status, dict) else 0
        if not device and blocked > 0:
            return {"enabled": False, "hidden": True, "blocked_devices_count": blocked}
        host = self.esp32.get_active_host() if self.esp32 else None
        return {
            "enabled": True,
            "online": online,
            "host": host or cfg.preferred_host or "",
            "ip": device.get("ip", "") if isinstance(device, dict) else "",
            "port": device.get("port", 80) if isinstance(device, dict) else 80,
            "paired": device.get("paired") if isinstance(device, dict) else None,
            "is_paired_to_self": bool(device.get("is_paired_to_self")) if isinstance(device, dict) else False,
            "needs_firmware_update": bool(device.get("needs_firmware_update")) if isinstance(device, dict) else False,
            "blocked_devices_count": blocked,
        }

    def _handle_set_camera(self, port: str) -> dict:
        """Update the active camera port in the in-memory config (runtime switch).

        Special port value ``"esp32"`` enables the ESP32 wireless camera and
        clears the local camera port so perception prefers the wireless feed.
        Any other value disables the ESP32 preference and sets a local port.
        """
        value = (port or "").strip()
        if value == "esp32":
            status = self.esp32.get_status() if self.esp32 else {}
            device = status.get("device") if isinstance(status, dict) else None
            if device and device.get("needs_firmware_update"):
                return {"ok": False, "error": "esp32_firmware_update_required", "result": {"esp32": self._esp32_camera_info()}}
            if device and device.get("is_paired_to_other"):
                return {"ok": False, "error": "esp32_paired_to_other", "result": {"esp32": self._esp32_camera_info()}}
            self.config.device_esp32.enabled = True
            self.config.camera.port = ""
            logger.info("camera.switched_to_esp32")
        else:
            self.config.device_esp32.enabled = False
            self.config.camera.port = value
            logger.info("camera.port_updated", port=value or "<disabled>")
        return {
            "ok": True,
            "result": {
                "active": "esp32" if self.config.device_esp32.enabled else self.config.camera.port,
                "camera_ready": bool(value) or self.config.device_esp32.enabled,
                "esp32": self._esp32_camera_info(),
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

    async def _handle_set_mic(self, mic_device: str) -> dict:
        """Hot-switch the server-side microphone used by wake/call mode."""
        value = str(mic_device or "").strip()
        if value and value != "esp32":
            try:
                int(value)
            except ValueError:
                return {"ok": False, "error": f"invalid mic device: {value}"}

        self.config.voice.mic_device = value
        try:
            from lampgo import personastore

            personastore.patch_overrides_toml({"voice": {"mic_device": value}})
        except Exception:
            logger.debug("server.mic_config_persist_failed", mic_device=value, exc_info=True)

        if self._wake_loop is not None:
            await self._wake_loop.set_mic_device(value)

        result = self._handle_list_mics()
        result["type"] = "set_mic"
        if result.get("ok") and isinstance(result.get("result"), dict):
            result["result"]["active"] = value
        return result

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
                self.config.llm,
                skill_specs,
                camera_config=self.config.camera,
                device_esp32_config=self.config.device_esp32,
                esp32_manager=self.esp32,
                recording_actions_prompt_provider=lambda: build_recording_actions_prompt(Path(self.config.recordings_dir)),
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
            self._use_virtual_motion()
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
                self._use_virtual_motion()
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
        self._start_idle_sway_scheduler()
        await self._ipc.start()
        if self.config.device_esp32.enabled:
            try:
                await self.esp32.start()
            except Exception:
                logger.exception("server.esp32_start_failed")
        self._setup_llm_router()
        self._state_writer.start(get_state=self._get_minimal_state)
        logger.info(
            "server.ready",
            skills=self.registry.list_ids(),
            motor_port="(disabled)" if self.config.no_hw else self.config.device.motor_port,
            socket=self.config.socket_path,
        )
        self._started = True

    async def reload_motor_runtime(self) -> dict[str, Any]:
        """Hot-reconnect the motor HAL and MotionRuntime after motor_port edits."""
        if not self._started:
            return {"ok": True, "skipped": True, "reason": "server_not_started"}

        async with self._motor_reload_lock:
            port = str(self.config.device.motor_port or "").strip()
            logger.info("server.motor_runtime_reload_starting", motor_port=port or "<disabled>")

            if self.executor.current_skill_id:
                await self.executor.cancel_current()

            if self.motion.is_running:
                try:
                    self.motion.stop_immediate()
                except Exception:
                    pass
            try:
                self.motion.stop()
            except Exception:
                logger.exception("server.motor_runtime_stop_failed")

            old_hal = self.hal
            await self._run_blocking_shutdown_step("hal.disconnect", old_hal.disconnect, timeout_s=3.0)

            self.hal = HardwareAbstraction(self.config.device)
            if not port:
                self.config.no_hw = True
                self._use_virtual_motion()
                logger.info("server.motor_runtime_reload_virtual", reason="empty_motor_port")
                return {
                    "ok": True,
                    "connected": False,
                    "mode": "virtual",
                    "reason": "empty_motor_port",
                }

            new_hal = HardwareAbstraction(self.config.device)
            try:
                await asyncio.to_thread(new_hal.connect)
            except Exception as exc:  # noqa: BLE001
                try:
                    await asyncio.to_thread(new_hal.disconnect)
                except Exception:
                    pass
                self.hal = HardwareAbstraction(self.config.device)
                self.config.no_hw = True
                self._use_virtual_motion()
                logger.warning(
                    "server.motor_runtime_reload_failed_virtual",
                    motor_port=port,
                    error=str(exc),
                )
                return {
                    "ok": False,
                    "connected": False,
                    "mode": "virtual",
                    "port": port,
                    "error": str(exc),
                }

            self.hal = new_hal
            self.motion = MotionRuntime(self.hal, self.safety, self.config.motion)
            self.config.no_hw = False
            self.motion.start()
            home = self.hal.get_calibration_home()
            if home is not None:
                from lampgo.skills.builtin.motion_skills import set_calibration_home

                set_calibration_home(home)
            logger.info("server.motor_runtime_reloaded", motor_port=port)
            return {
                "ok": True,
                "connected": True,
                "mode": "hardware",
                "port": port,
            }

    def _get_minimal_state(self) -> MinimalState:
        camera_connected = bool(self.config.camera.port.strip())
        mic_active = self._wake_loop is not None
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
            result = await self.executor.invoke("return_safe", ctx, velocity=STARTUP_HOME_VELOCITY)
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
            client = LLMClient(
                self.config.llm,
                skill_specs,
                camera_config=self.config.camera,
                device_esp32_config=self.config.device_esp32,
                esp32_manager=self.esp32,
                recording_actions_prompt_provider=lambda: build_recording_actions_prompt(Path(self.config.recordings_dir)),
            )
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
            client = LLMClient(
                self.config.llm,
                skill_specs,
                camera_config=self.config.camera,
                device_esp32_config=self.config.device_esp32,
                esp32_manager=self.esp32,
                recording_actions_prompt_provider=lambda: build_recording_actions_prompt(Path(self.config.recordings_dir)),
            )
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
        await self._stop_idle_sway_scheduler()
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
        await self._stop_voice_loop()
        await self._ipc.stop()
        try:
            await self.esp32.shutdown()
        except Exception:
            logger.exception("server.esp32_shutdown_failed")
        if self.config.no_hw:
            self.motion.stop()
        else:
            self.motion.stop()
            await self._run_blocking_shutdown_step("led.off", self.led.off)
            await self._run_blocking_shutdown_step("led.disconnect", self.led.disconnect)
            await self._run_blocking_shutdown_step("hal.disconnect", self.hal.disconnect, timeout_s=3.0)
        self._started = False
        logger.info("server.stopped")

    async def run_forever(self) -> None:
        await self.start()
        local_url = ""
        if self.config.web_enabled:
            local_url = await self._start_web_gateway(print_banner=False) or ""
            vc = self.config.voice
            if vc.wake_word and vc.livekit_url and vc.livekit_api_key and vc.livekit_api_secret:
                await self._start_voice_loop()
            if local_url:
                self._print_connection_banner(local_url)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        logger.info("server.running (Ctrl+C to stop)")
        await stop.wait()
        await self.shutdown()

    async def _start_web_gateway(self, *, print_banner: bool = True) -> str | None:
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
            if print_banner:
                self._print_connection_banner(local_url)
            return local_url
        except ImportError:
            logger.error("server.web_missing_deps (pip install starlette uvicorn websockets)")
            return None
        except Exception:
            logger.exception("server.web_start_failed")
            return None

    def _print_connection_banner(self, local_url: str) -> None:
        """Print a clear banner so users know how to wire up OpenClaw when port changes."""
        def _mark(ok: bool) -> str:
            return "✓" if ok else "✗"

        try:
            from lampgo.bridge.openclaw_installer import detect_openclaw_integration

            status = detect_openclaw_integration()
            overall = status.overall
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

        vc = self.config.voice
        livekit_configured = bool(vc.livekit_url and vc.livekit_api_key and vc.livekit_api_secret)
        wake_configured = bool(vc.wake_word)
        agent_running = bool(self._agent_sdk and self._agent_sdk.is_running)
        agent_ready = bool(self._agent_sdk and getattr(self._agent_sdk, "is_ready", False))
        wake_loop_running = bool(self._wake_loop and self._voice_task and not self._voice_task.done())
        esp32_status = self.esp32.get_status() if self.esp32 else {"online": False}
        esp32_online = bool(esp32_status.get("online"))
        esp32_device = esp32_status.get("device") or {}
        esp32_label = (
            esp32_device.get("host")
            or esp32_device.get("ip")
            or esp32_status.get("preferred_host")
            or "未发现"
        )
        esp32_seen = bool(esp32_device or esp32_status.get("preferred_host"))
        voice_ready = livekit_configured and wake_configured and agent_ready and wake_loop_running
        livekit_lines = [
            f"LiveKit 配置      : {_mark(livekit_configured)} {vc.livekit_url or '未配置'}",
            f"LiveKit worker    : {_mark(agent_ready)} {'registered worker' if agent_ready else ('启动中/未就绪' if agent_running else '未启动')}",
            f"唤醒监听          : {_mark(wake_loop_running and wake_configured)} {vc.wake_word or '未启用'}",
        ]
        if self.config.device_esp32.enabled:
            livekit_lines.append(
                f"ESP32 设备        : {_mark(esp32_online or esp32_seen)} {esp32_label}{'' if esp32_online else '（等待健康检查）'}"
            )
        livekit_lines.append(f"通话状态          : {_mark(voice_ready)} {'ready' if voice_ready else 'not ready'}")

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
        lines.extend(livekit_lines)
        lines += [
            "一键修复集成     : uv run lampgo install-openclaw --yes",
            "──────────────────────────────",
            "",
        ]
        print("\n".join(lines), flush=True)

    async def _start_voice_loop(self) -> None:
        try:
            from lampgo.voice.wake_loop import WakeLoop

            self._wake_loop = WakeLoop(self)
            self._voice_task = asyncio.create_task(self._wake_loop.run())
            logger.info(
                "server.wake_loop_started",
                wake_word=self.config.voice.wake_word,
                livekit_url=self.config.voice.livekit_url,
            )
            await self._start_agent_sdk()
        except Exception:
            logger.exception("server.wake_loop_failed")

    async def _stop_voice_loop(self) -> None:
        if self._agent_sdk is not None:
            await self._agent_sdk.stop()
            self._agent_sdk = None
        if self._wake_loop is not None:
            self._wake_loop.stop()
            self._wake_loop = None
        if self._voice_task is not None:
            self._voice_task.cancel()
            try:
                await self._voice_task
            except (asyncio.CancelledError, Exception):
                pass
            self._voice_task = None
        logger.info("server.voice_loop_stopped")

    async def restart_voice_loop(self) -> None:
        """Debounced hot-reload: coalesce rapid saves into a single restart."""
        if self._voice_reload_handle is not None:
            self._voice_reload_handle.cancel()
        loop = asyncio.get_running_loop()
        self._voice_reload_handle = loop.call_later(
            1.0, lambda: asyncio.create_task(self._do_restart_voice_loop())
        )

    async def _do_restart_voice_loop(self) -> None:
        self._voice_reload_handle = None
        await self._stop_voice_loop()
        vc = self.config.voice
        if not vc.wake_word or not vc.livekit_url:
            logger.info("server.voice_loop_disabled (wake_word or livekit_url empty)")
            return
        missing = [f for f in ("livekit_api_key", "livekit_api_secret") if not getattr(vc, f, "")]
        if missing:
            logger.warning("server.voice_loop_incomplete", missing=missing)
            return
        await self._start_voice_loop()

    async def _start_agent_sdk(self) -> None:
        """Launch the Lampgo LiveKit Agent SDK as a managed subprocess."""
        try:
            from lampgo.voice.agent_sdk import AgentSDKManager

            if self._agent_sdk is not None and self._agent_sdk.is_running:
                return
            self._agent_sdk = AgentSDKManager(
                self.config.voice,
                web_port=self.config.web.port,
            )
            started = await self._agent_sdk.start()
            if not started:
                logger.warning("server.agent_sdk_not_started", error=self._agent_sdk.last_error)
                return
            ready = await self._agent_sdk.wait_ready(timeout_s=20.0)
            if ready:
                logger.info("server.agent_sdk_ready")
            else:
                logger.warning("server.agent_sdk_ready_timeout")
        except Exception:
            logger.exception("server.agent_sdk_start_failed")

    async def ensure_agent_sdk_ready(self, *, timeout_s: float = 10.0) -> tuple[bool, str]:
        """Ensure the LiveKit Agent SDK is running for a manual browser call."""
        if self._agent_sdk is None or not self._agent_sdk.is_running:
            await self._start_agent_sdk()
        if self._agent_sdk is None:
            return False, "voice agent SDK is not configured"
        if not self._agent_sdk.is_running:
            return False, self._agent_sdk.last_error or "voice agent SDK is not running"
        ready = await self._agent_sdk.wait_ready(timeout_s=timeout_s)
        if not ready:
            return False, "voice agent SDK is still starting"
        return True, ""


async def run_server(config: LampgoConfig) -> None:
    server = LampgoServer(config)
    await server.run_forever()
