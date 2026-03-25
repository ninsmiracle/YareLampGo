"""Main entry point — creates all components and runs the asyncio loop."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import structlog

from lampgo.core.config import LampgoConfig
from lampgo.core.events import EventBus
from lampgo.core.hal import HardwareAbstraction
from lampgo.core.led import LEDController
from lampgo.core.motion import MotionRuntime
from lampgo.core.safety import SafetyKernel
from lampgo.core.types import JointState
from lampgo.bridge.openclaw import OpenClawAdapter
from lampgo.skills.base import SkillContext
from lampgo.skills.builtin.expression_skills import SetExpressionSkill
from lampgo.skills.builtin.motion_skills import EStopSkill, MoveToSkill, ReturnSafeSkill
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
        self.led = LEDController(config.led)
        self.fsm = StateMachine()
        self.registry = SkillRegistry()
        self.executor = SkillExecutor(self.registry, self.events)
        self.openclaw = OpenClawAdapter(self.registry, self.executor)

    def _register_builtin_skills(self) -> None:
        recordings_dir = Path(self.config.recordings_dir)
        self.registry.register(MoveToSkill())
        self.registry.register(ReturnSafeSkill())
        self.registry.register(EStopSkill())
        self.registry.register(PlayRecordingSkill(recordings_dir))
        self.registry.register(SetExpressionSkill())

    def make_context(self) -> SkillContext:
        return SkillContext(
            motion=self.motion,
            led=self.led,
            events=self.events,
            state=self.motion.current_state,
        )

    async def start(self) -> None:
        logger.info("server.starting")
        self.hal.connect()
        self.led.connect()
        self.motion.start()
        self._register_builtin_skills()
        logger.info(
            "server.ready",
            skills=self.registry.list_ids(),
            motor_port=self.config.device.motor_port,
        )

    async def shutdown(self) -> None:
        logger.info("server.shutting_down")
        self.motion.stop()
        self.led.off()
        self.led.disconnect()
        self.hal.disconnect()
        logger.info("server.stopped")

    async def run_forever(self) -> None:
        await self.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        logger.info("server.running (Ctrl+C to stop)")
        await stop.wait()
        await self.shutdown()


async def run_server(config: LampgoConfig) -> None:
    server = LampgoServer(config)
    await server.run_forever()
