"""WakeLoop — wake-word activation for local mic or ESP32-side WakeNet.

Pipeline:
  [ESP32 WakeNet /ws/events] or [local mic → openWakeWord]
                → publish WakeWordDetected
                → browser call joins LiveKit and publishes audio
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
import json
from collections import deque
from typing import TYPE_CHECKING

import structlog

from lampgo.voice.livekit_bridge import ConversationState, LiveKitBridge

if TYPE_CHECKING:
    from lampgo.server import LampgoServer

logger = structlog.get_logger(__name__)

SAMPLE_RATE = 16000
RING_BUFFER_SECONDS = 1.0
CHUNK_BYTES = 960  # 30ms × 16kHz × 16-bit = 960 bytes per ESP32 frame
RING_BUFFER_CHUNKS = int(RING_BUFFER_SECONDS * SAMPLE_RATE * 2 / CHUNK_BYTES)
DEVICE_WAKE_HEALTH_S = 3.0


class WakeLoop:
    """Continuous wake-word listener for local mic or ESP32 device events."""

    def __init__(self, server: LampgoServer) -> None:
        self._server = server
        cfg = server.config

        self._detector = None
        from lampgo.voice.agent_sdk import AGENT_SDK_PORT

        self._bridge = LiveKitBridge(cfg.voice, agent_sdk_port=AGENT_SDK_PORT)
        self._bridge.set_state_callback(self._on_conversation_state)

        self._ring_buffer: deque[bytes] = deque(maxlen=RING_BUFFER_CHUNKS)
        self._capture_is_esp32 = False
        self._capture = self._build_capture(cfg)
        self._running = False
        self._bridge_start_task: asyncio.Task | None = None
        self._device_wake_task: asyncio.Task | None = None
        self._capture_lock = asyncio.Lock()
        self._browser_call_active = False
        self._next_esp32_retry_at = 0.0

    def _build_capture(self, cfg):
        """Pick ESP32 event-only wake (preferred) or local mic fallback."""
        prefer_esp32 = cfg.voice.mic_device == "esp32" or cfg.device_esp32.mic_enabled
        if prefer_esp32 and hasattr(self._server, "esp32") and self._server.esp32:
            esp32 = self._server.esp32
            if esp32.is_online():
                logger.info("wake_loop.using_esp32_mic")
                self._capture_is_esp32 = True
                return None

            logger.info("wake_loop.esp32_offline_fallback")

        from lampgo.voice.audio import AudioCapture

        mic_dev = None
        if cfg.voice.mic_device and cfg.voice.mic_device != "esp32":
            try:
                mic_dev = int(cfg.voice.mic_device)
            except ValueError:
                mic_dev = cfg.voice.mic_device
        self._capture_is_esp32 = False
        self._ensure_local_detector()
        return AudioCapture(sample_rate=SAMPLE_RATE, device=mic_dev)

    def _ensure_local_detector(self) -> None:
        if self._detector is not None:
            return
        from lampgo.voice.wakeword import WakeWordDetector

        self._detector = WakeWordDetector(
            model_name=self._server.config.voice.wake_word or "hey_jarvis",
            threshold=0.5,
        )

    @property
    def bridge(self) -> LiveKitBridge:
        return self._bridge

    @property
    def conversation_state(self) -> ConversationState:
        return self._bridge.state

    async def run(self) -> None:
        """Main loop: capture audio, detect wake word, bridge to LiveKit."""
        if self._capture is not None:
            self._capture.start()
        self._running = True
        if self._capture_is_esp32:
            self._start_device_wake_listener()
        logger.info(
            "wake_loop.started",
            wake_word=self._server.config.voice.wake_word,
            detector_ready=(self._detector.is_ready if self._detector else False),
            device_wake=self._capture_is_esp32,
            livekit_url=self._server.config.voice.livekit_url,
        )

        try:
            while self._running:
                await self._maybe_switch_to_esp32()
                if self._capture_is_esp32:
                    await asyncio.sleep(0.05)
                    continue
                capture = self._capture
                if capture is None:
                    await asyncio.sleep(0.05)
                    continue
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

                if self._capture_is_esp32:
                    continue

                self._ensure_local_detector()
                if self._detector and self._detector.feed(chunk):
                    await self._handle_wake_detected(
                        model=self._server.config.voice.wake_word or "",
                        source="local",
                    )

        except asyncio.CancelledError:
            pass
        finally:
            if self._device_wake_task and not self._device_wake_task.done():
                self._device_wake_task.cancel()
                try:
                    await self._device_wake_task
                except asyncio.CancelledError:
                    pass
                self._device_wake_task = None
            if self._bridge_start_task and not self._bridge_start_task.done():
                self._bridge_start_task.cancel()
                try:
                    await self._bridge_start_task
                except asyncio.CancelledError:
                    pass
            if self._bridge.state != ConversationState.IDLE:
                await self._bridge.stop_conversation()
            if self._capture is not None:
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
            if self._capture is not None:
                self._capture.start()
            if self._device_wake_task and not self._device_wake_task.done():
                self._device_wake_task.cancel()
                self._device_wake_task = None
            if self._running and self._capture_is_esp32:
                self._start_device_wake_listener()
            if old_capture is not None:
                try:
                    old_capture.stop()
                except Exception:
                    logger.debug("wake_loop.old_capture_stop_failed", exc_info=True)
            self._ring_buffer.clear()
            logger.info("wake_loop.mic_device_switched", mic_device=mic_device or "default")

    async def _maybe_switch_to_esp32(self) -> None:
        """Recover from startup races where ESP32 discovery completes late."""
        if self._capture_is_esp32:
            return
        cfg = self._server.config
        prefer_esp32 = cfg.voice.mic_device == "esp32" or cfg.device_esp32.mic_enabled
        if not prefer_esp32:
            return
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < self._next_esp32_retry_at:
            return
        self._next_esp32_retry_at = now + 3.0
        if hasattr(self._server, "esp32") and self._server.esp32 and self._server.esp32.is_online():
            logger.info("wake_loop.esp32_online_switching")
            await self.set_mic_device("esp32")

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

    def _start_device_wake_listener(self) -> None:
        if self._device_wake_task and not self._device_wake_task.done():
            return
        self._device_wake_task = asyncio.create_task(self._device_wake_event_loop())

    async def _device_wake_event_loop(self) -> None:
        """Listen for ESP32-side wake detections."""
        try:
            import websockets
        except ImportError:
            logger.error("wake_loop.no_websockets", msg="Install websockets: uv add websockets")
            return

        from lampgo.device.audio_stream import build_ws_events_url

        delay = 1.0
        while self._running and self._capture_is_esp32:
            url = build_ws_events_url(self._server.esp32)
            if not url:
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 30.0)
                continue

            try:
                logger.info("wake_loop.device_wake_connecting", url=url)
                async with websockets.connect(
                    url, open_timeout=5, close_timeout=2, ping_interval=None
                ) as ws:
                    logger.info("wake_loop.device_wake_connected", url=url)
                    delay = 1.0
                    while self._running and self._capture_is_esp32:
                        try:
                            message = await asyncio.wait_for(
                                ws.recv(), timeout=DEVICE_WAKE_HEALTH_S
                            )
                        except asyncio.TimeoutError:
                            if not await self._device_wake_connection_alive():
                                logger.warning("wake_loop.device_wake_stale", url=url)
                                break
                            continue
                        if isinstance(message, bytes):
                            message = message.decode("utf-8", errors="ignore")
                        try:
                            payload = json.loads(message)
                        except json.JSONDecodeError:
                            logger.debug("wake_loop.device_wake_bad_json", message=message)
                            continue
                        if payload.get("type") != "wake_word_detected":
                            continue
                        model = payload.get("model") or self._server.config.voice.wake_word or ""
                        await self._handle_wake_detected(model=f"esp32:{model}", source="esp32")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("wake_loop.device_wake_error", url=url, error=str(exc))

            if self._running and self._capture_is_esp32:
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 30.0)

    async def _device_wake_connection_alive(self) -> bool:
        """Best-effort check that the ESP32 still sees our wake event socket."""
        try:
            status_code, body, _ = await self._server.esp32.proxy_get("/device/status")
        except Exception:
            logger.debug("wake_loop.device_wake_health_error", exc_info=True)
            return False
        if status_code != 200 or not isinstance(body, dict):
            return False
        clients = body.get("wake_event_clients")
        return clients is None or int(clients or 0) > 0

    async def _handle_wake_detected(self, *, model: str, source: str) -> None:
        if self._bridge.state != ConversationState.IDLE:
            return
        if self._bridge_start_task and not self._bridge_start_task.done():
            return

        logger.info("wake_loop.wake_word_detected", model=model, source=source)
        if self._detector:
            self._detector.reset()

        self._ring_buffer.clear()

        from lampgo.core.events import WakeWordDetected

        await self._server.events.publish(WakeWordDetected(model=model))
        await asyncio.sleep(0)

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
