"""WakeLoop — continuous ESP32 mic listener with wake-word activation.

Pipeline:
  [ESP32 mic] → Ring Buffer (≈1 s)
             → openWakeWord detector
                → detected? → LiveKitBridge.start_conversation(backfill)
                → bridge forwards audio to LiveKit room
                → Lampgo Agent SDK handles ASR/TTS via LiveKit
                → SDK calls lampgo /v1/chat/completions for LLM
                → Exit conditions met? → stop_conversation()

This loop replaces :class:`VoiceLoop` when ``config.voice.wake_word``
is configured.  Unlike VoiceLoop it never does local STT/TTS — all
speech recognition and synthesis happen inside the LiveKit room via the
Lampgo Agent SDK.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING

import structlog

from lampgo.voice.livekit_bridge import ConversationState, LiveKitBridge
from lampgo.voice.wakeword import WakeWordDetector

if TYPE_CHECKING:
    from lampgo.server import LampgoServer

logger = structlog.get_logger(__name__)

SAMPLE_RATE = 16000
RING_BUFFER_SECONDS = 1.0
CHUNK_BYTES = 960  # 30ms × 16kHz × 16-bit = 960 bytes per ESP32 frame
RING_BUFFER_CHUNKS = int(RING_BUFFER_SECONDS * SAMPLE_RATE * 2 / CHUNK_BYTES)


class WakeLoop:
    """Continuous wake-word listener that bridges ESP32 audio into LiveKit."""

    def __init__(self, server: LampgoServer) -> None:
        self._server = server
        cfg = server.config

        self._detector = WakeWordDetector(
            model_name=cfg.voice.wake_word or "hey_jarvis",
            threshold=0.5,
        )
        from lampgo.voice.agent_sdk import AGENT_SDK_PORT

        self._bridge = LiveKitBridge(cfg.voice, agent_sdk_port=AGENT_SDK_PORT)
        self._bridge.set_state_callback(self._on_conversation_state)

        self._ring_buffer: deque[bytes] = deque(maxlen=RING_BUFFER_CHUNKS)
        self._capture = self._build_capture(cfg)
        self._running = False
        self._bridge_start_task: asyncio.Task | None = None
        self._capture_lock = asyncio.Lock()
        self._browser_call_active = False

    def _build_capture(self, cfg):
        """Pick ESP32 mic (preferred) or local mic fallback."""
        prefer_esp32 = cfg.voice.mic_device == "esp32" or cfg.device_esp32.mic_enabled
        if prefer_esp32 and hasattr(self._server, "esp32") and self._server.esp32:
            esp32 = self._server.esp32
            if esp32.is_online():
                from lampgo.device.audio_stream import Esp32AudioCapture

                logger.info("wake_loop.using_esp32_mic")
                return Esp32AudioCapture(esp32)

            logger.info("wake_loop.esp32_offline_fallback")

        from lampgo.voice.audio import AudioCapture

        mic_dev = None
        if cfg.voice.mic_device and cfg.voice.mic_device != "esp32":
            try:
                mic_dev = int(cfg.voice.mic_device)
            except ValueError:
                mic_dev = cfg.voice.mic_device
        return AudioCapture(sample_rate=SAMPLE_RATE, device=mic_dev)

    @property
    def bridge(self) -> LiveKitBridge:
        return self._bridge

    @property
    def conversation_state(self) -> ConversationState:
        return self._bridge.state

    async def run(self) -> None:
        """Main loop: capture audio, detect wake word, bridge to LiveKit."""
        self._capture.start()
        self._running = True
        logger.info(
            "wake_loop.started",
            wake_word=self._server.config.voice.wake_word,
            detector_ready=self._detector.is_ready,
            livekit_url=self._server.config.voice.livekit_url,
        )

        try:
            while self._running:
                capture = self._capture
                chunk = await capture.aread_chunk(timeout=0.05)
                if chunk is None:
                    await asyncio.sleep(0.01)
                    continue

                self._ring_buffer.append(chunk)

                if self._browser_call_active:
                    await self._relay_to_frontend(chunk)
                    continue

                if self._bridge.state in (ConversationState.JOINING, ConversationState.ACTIVE):
                    self._bridge.feed_audio(chunk)
                    continue

                if self._bridge.state != ConversationState.IDLE:
                    continue

                if self._detector.feed(chunk):
                    logger.info("wake_loop.wake_word_detected")
                    self._detector.reset()
                    self._ring_buffer.clear()
                    from lampgo.core.events import WakeWordDetected

                    await self._server.events.publish(WakeWordDetected(model=self._server.config.voice.wake_word or ""))
                    await asyncio.sleep(0)

        except asyncio.CancelledError:
            pass
        finally:
            if self._bridge_start_task and not self._bridge_start_task.done():
                self._bridge_start_task.cancel()
                try:
                    await self._bridge_start_task
                except asyncio.CancelledError:
                    pass
            if self._bridge.state != ConversationState.IDLE:
                await self._bridge.stop_conversation()
            self._capture.stop()
            self._running = False
            logger.info("wake_loop.stopped")

    def stop(self) -> None:
        self._running = False

    async def set_mic_device(self, mic_device: str) -> None:
        """Hot-swap the microphone capture device used by wake/call mode."""
        async with self._capture_lock:
            old_capture = self._capture
            self._capture = self._build_capture(self._server.config)
            self._capture.start()
            try:
                old_capture.stop()
            except Exception:
                logger.debug("wake_loop.old_capture_stop_failed", exc_info=True)
            self._ring_buffer.clear()
            logger.info("wake_loop.mic_device_switched", mic_device=mic_device or "default")

    def start_browser_relay(self) -> None:
        """Enable ESP32 audio relay to the browser for LiveKit publishing."""
        self._browser_call_active = True
        logger.info("wake_loop.browser_relay_started")

    def stop_browser_relay(self) -> None:
        """Disable ESP32 audio relay to the browser."""
        self._browser_call_active = False
        logger.info("wake_loop.browser_relay_stopped")

    async def _relay_to_frontend(self, chunk: bytes) -> None:
        """Publish an ESP32 PCM chunk to the EventBus for WS relay."""
        from lampgo.core.events import Esp32AudioRelay

        try:
            await self._server.events.publish(Esp32AudioRelay(pcm=chunk))
        except Exception:
            logger.debug("wake_loop.relay_error", exc_info=True)

    async def end_conversation(self) -> None:
        """Manually end the current conversation (called from frontend)."""
        if self._bridge.state in (ConversationState.ACTIVE, ConversationState.JOINING):
            logger.info("wake_loop.manual_end")
            await self._bridge.stop_conversation()

    async def _start_bridge_conversation(self, backfill: deque[bytes]) -> None:
        """Start LiveKit without blocking the microphone capture loop."""
        try:
            ok = await self._bridge.start_conversation(backfill=backfill)
            if not ok:
                logger.warning("wake_loop.bridge_start_failed")
        finally:
            self._bridge_start_task = None

    async def _on_conversation_state(self, state: ConversationState) -> None:
        """Publish conversation state changes to the EventBus."""
        from lampgo.core.events import ConversationStateChanged

        try:
            await self._server.events.publish(
                ConversationStateChanged(state=state.value)
            )
        except Exception:
            logger.debug("wake_loop.event_publish_error", exc_info=True)
