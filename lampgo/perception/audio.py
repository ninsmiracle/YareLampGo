"""Voice Activity Detection (VAD) — optional audio perception module.

Publishes VoiceActivity events when speech is detected.
Intended for wake-word or speech-start triggers.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from lampgo.core.events import Event, EventBus

logger = structlog.get_logger(__name__)


@dataclass
class VoiceActivity(Event):
    is_speaking: bool
    timestamp: float = 0.0


class VADDetector:
    """Stub VAD detector — to be replaced with a real implementation (e.g. Silero VAD)."""

    def __init__(self, events: EventBus) -> None:
        self._events = events
        self._running = False

    async def start(self) -> None:
        logger.info("vad.started (stub — no real audio processing)")
        self._running = True

    async def stop(self) -> None:
        self._running = False
        logger.info("vad.stopped")
