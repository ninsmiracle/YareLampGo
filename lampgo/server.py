"""Main entry point — creates all components and runs the asyncio loop.

The server owns:
  - Hardware (HAL, LED, Motion, Safety)
  - Skill system (Registry, Executor, FSM)
  - IPC server (Unix socket for CLI / OpenClaw / scripts)
  - IntentRouter (keyword + optional fast LLM)
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any

import structlog

from lampgo.bridge.openclaw import OpenClawAdapter
from lampgo.core.config import LampgoConfig
from lampgo.core.events import EventBus
from lampgo.core.hal import HardwareAbstraction
from lampgo.core.config import LEDConfig
from lampgo.core.led import LEDController
from lampgo.core.motion import MotionRuntime
from lampgo.core.safety import SafetyKernel
from lampgo.core.config import WebConfig
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
from lampgo.skills.executor import SkillExecutor
from lampgo.skills.fsm import StateMachine
from lampgo.skills.registry import SkillRegistry

logger = structlog.get_logger(__name__)


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
        self.openclaw = OpenClawAdapter(self.registry, self.executor)
        self.router = IntentRouter()
        self._ipc = IPCServer(self.handle_request, socket_path=config.socket_path)
        self._voice_task: asyncio.Task | None = None
        self._web_gateway = None

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
            return await self._handle_text(data)

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

        return {"ok": False, "error": f"unknown command: {cmd}"}

    async def _handle_invoke(self, data: dict) -> dict:
        skill_id = data.get("skill_id", "")
        params = data.get("params", {})

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

    async def _handle_text(self, data: dict) -> dict:
        """Route free text through the IntentRouter, then invoke or reply."""
        text = data.get("input", "").strip()
        if not text:
            return {"ok": False, "error": "empty input"}

        intent = await self.router.aroute(text)

        if intent.intent_type == IntentType.CHAT:
            return {"ok": True, "result": {"type": "chat", "response": intent.chat_response}}

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
                },
            }

        return {
            "ok": True,
            "result": {
                "type": "complex",
                "response": "This request is too complex for the fast path. Please use OpenClaw.",
                "original_text": text,
            },
        }

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
            },
        }

    def _handle_skills(self) -> dict:
        skills = []
        for skill in self.registry.list_skills():
            skills.append(
                {
                    "skill_id": skill.skill_id,
                    "description": skill.description,
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
            )
        return {"ok": True, "result": {"skills": skills}}

    async def start(self) -> None:
        logger.info("server.starting")
        self.hal.connect()
        self.led.connect()
        self.motion.start()

        self._register_builtin_skills()
        if self.config.home_on_start:
            await self._home_on_start()
        await self._ipc.start()
        self._setup_llm_router()
        logger.info(
            "server.ready",
            skills=self.registry.list_ids(),
            motor_port=self.config.device.motor_port,
            socket=self.config.socket_path,
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
            client = LLMClient(self.config.llm, skill_specs)
            self.router.set_llm_client(client)
            logger.info("server.llm_router_enabled", model=self.config.llm.fast_model)
        except Exception:
            logger.exception("server.llm_router_setup_failed")

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
        if self._voice_task is not None:
            self._voice_task.cancel()
            try:
                await self._voice_task
            except asyncio.CancelledError:
                pass
        await self._ipc.stop()
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
            logger.info(
                "server.web_started",
                url=f"http://localhost:{self.config.web.port}",
            )
        except ImportError:
            logger.error("server.web_missing_deps (pip install starlette uvicorn websockets)")
        except Exception:
            logger.exception("server.web_start_failed")

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
