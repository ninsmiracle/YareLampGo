"""WakeLoop — wake-word activation from ESP32-side WakeNet.

Pipeline:
  [ESP32 WakeNet /ws/events]
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

DEVICE_WAKE_IDLE_LOG_S = 30.0
ESP32_WAKE_MODEL_BY_CONFIG = {
    "hey_jarvis": "wn9_jarvis_tts",
    "wn9_jarvis_tts": "wn9_jarvis_tts",
    "wn9_xiaomeitongxue_tts": "wn9_xiaomeitongxue_tts",
    "wn9_xiaoyaxiaoya_tts2": "wn9_xiaoyaxiaoya_tts2",
    "wn9_xiaoluxiaolu_tts2": "wn9_xiaoluxiaolu_tts2",
    "wn9_hixiaoxing_tts": "wn9_hixiaoxing_tts",
}


class WakeLoop:
    """Continuous wake-word listener for ESP32 device events."""

    def __init__(self, server: LampgoServer) -> None:
        self._server = server
        cfg = server.config

        from lampgo.voice.agent_sdk import AGENT_SDK_PORT

        self._bridge = LiveKitBridge(cfg.voice, agent_sdk_port=AGENT_SDK_PORT)
        self._bridge.set_state_callback(self._on_conversation_state)

        # Kept for the existing manual start_conversation path; ESP32 audio is
        # published by the browser relay, so there is no local pre-roll buffer.
        self._ring_buffer: deque[bytes] = deque(maxlen=0)
        self._capture_is_esp32 = self._esp32_online()
        self._running = False
        self._bridge_start_task: asyncio.Task | None = None
        self._device_wake_task: asyncio.Task | None = None
        self._device_wake_resume_task: asyncio.Task | None = None
        self._capture_lock = asyncio.Lock()
        self._next_esp32_retry_at = 0.0
        self._device_wake_paused_until = 0.0
        self._next_wake_model_sync_at = 0.0
        if not self._capture_is_esp32:
            logger.info("wake_loop.esp32_waiting", wake_word=cfg.voice.wake_word)

    def _esp32_online(self) -> bool:
        if not (hasattr(self._server, "esp32") and self._server.esp32):
            return False
        has_active = getattr(self._server.esp32, "has_active_device", None)
        if callable(has_active):
            return bool(has_active())
        return bool(self._server.esp32.is_online())

    @property
    def bridge(self) -> LiveKitBridge:
        return self._bridge

    @property
    def conversation_state(self) -> ConversationState:
        return self._bridge.state

    async def run(self) -> None:
        """Main loop: listen for ESP32 wake events and recover after discovery."""
        self._running = True
        if self._capture_is_esp32:
            self._start_device_wake_listener()
        logger.info(
            "wake_loop.started",
            wake_word=self._server.config.voice.wake_word,
            device_wake=self._capture_is_esp32,
            livekit_url=self._server.config.voice.livekit_url,
        )

        try:
            while self._running:
                await self._maybe_switch_to_esp32()
                if (
                    self._capture_is_esp32
                    and not self._device_wake_paused()
                    and (self._device_wake_task is None or self._device_wake_task.done())
                ):
                    self._start_device_wake_listener()
                await asyncio.sleep(0.05 if self._capture_is_esp32 else 0.25)

        except asyncio.CancelledError:
            pass
        finally:
            if self._device_wake_resume_task and not self._device_wake_resume_task.done():
                self._device_wake_resume_task.cancel()
                try:
                    await self._device_wake_resume_task
                except asyncio.CancelledError:
                    pass
                self._device_wake_resume_task = None
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
            self._running = False
            logger.info("wake_loop.stopped")

    def stop(self) -> None:
        self._running = False

    async def set_mic_device(self, mic_device: str) -> None:
        """Refresh the ESP32 wake listener after voice/device config changes."""
        async with self._capture_lock:
            self._capture_is_esp32 = self._esp32_online()
            if self._device_wake_task and not self._device_wake_task.done():
                self._device_wake_task.cancel()
                self._device_wake_task = None
            if self._device_wake_resume_task and not self._device_wake_resume_task.done():
                self._device_wake_resume_task.cancel()
                self._device_wake_resume_task = None
            if self._running and self._capture_is_esp32:
                self._start_device_wake_listener()
            self._ring_buffer.clear()
            logger.info(
                "wake_loop.esp32_wake_refreshed",
                mic_device=mic_device or "default",
                device_wake=self._capture_is_esp32,
            )

    async def _maybe_switch_to_esp32(self) -> None:
        """Recover from startup races where ESP32 discovery completes late."""
        if self._capture_is_esp32:
            return
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < self._next_esp32_retry_at:
            return
        self._next_esp32_retry_at = now + 3.0
        if self._esp32_online():
            logger.info("wake_loop.esp32_online_switching")
            await self.set_mic_device("esp32")

    def _start_device_wake_listener(self) -> None:
        if self._device_wake_paused():
            return
        if self._device_wake_task and not self._device_wake_task.done():
            return
        self._device_wake_task = asyncio.create_task(self._device_wake_event_loop())

    def pause_device_wake_listener(self, duration_s: float = 60.0) -> None:
        """Temporarily close the ESP32 wake WS while call audio uses /ws/audio."""
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            now = 0.0
        self._device_wake_paused_until = max(self._device_wake_paused_until, now + duration_s)
        if (
            self._device_wake_task
            and not self._device_wake_task.done()
            and self._device_wake_task is not asyncio.current_task()
        ):
            self._device_wake_task.cancel()
        if self._running and self._capture_is_esp32:
            if self._device_wake_resume_task is None or self._device_wake_resume_task.done():
                self._device_wake_resume_task = asyncio.create_task(self._resume_device_wake_when_pause_expires())
        logger.info("wake_loop.device_wake_paused", duration_s=duration_s)

    def resume_device_wake_listener(self) -> None:
        """Resume ESP32 wake events after a browser-managed call ends."""
        self._device_wake_paused_until = 0.0
        if self._device_wake_resume_task and not self._device_wake_resume_task.done():
            self._device_wake_resume_task.cancel()
            self._device_wake_resume_task = None
        if self._running and self._capture_is_esp32:
            self._start_device_wake_listener()
        logger.info("wake_loop.device_wake_resumed")

    def _device_wake_paused(self) -> bool:
        if self._device_wake_paused_until <= 0:
            return False
        return asyncio.get_running_loop().time() < self._device_wake_paused_until

    async def _resume_device_wake_when_pause_expires(self) -> None:
        """Restart the ESP32 wake listener when a timed pause naturally expires."""
        try:
            while self._running and self._capture_is_esp32:
                paused_until = self._device_wake_paused_until
                if paused_until <= 0:
                    return
                now = asyncio.get_running_loop().time()
                remaining = paused_until - now
                if remaining <= 0:
                    self._device_wake_paused_until = 0.0
                    self._start_device_wake_listener()
                    logger.info("wake_loop.device_wake_pause_expired")
                    return
                await asyncio.sleep(min(remaining, 1.0))
        except asyncio.CancelledError:
            raise

    async def _device_wake_event_loop(self) -> None:
        """Listen for ESP32-side wake detections."""
        try:
            import websockets
        except ImportError:
            logger.error("wake_loop.no_websockets", msg="Install websockets: uv add websockets")
            return

        from lampgo.device.audio_stream import build_ws_events_url, redact_ws_owner_token

        delay = 1.0
        while self._running and self._capture_is_esp32:
            if self._device_wake_paused():
                await asyncio.sleep(0.25)
                continue
            await self._sync_device_wake_model()
            claim_owner = getattr(self._server.esp32, "claim_owner", None)
            if callable(claim_owner):
                try:
                    ok = await claim_owner(reason="wake_listener")
                except Exception:
                    logger.debug("wake_loop.device_claim_failed", exc_info=True)
                    ok = False
                if not ok:
                    logger.warning("wake_loop.device_claim_denied")
                    await asyncio.sleep(delay)
                    delay = min(delay * 1.5, 30.0)
                    continue
            url = build_ws_events_url(self._server.esp32)
            if not url:
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 30.0)
                continue
            safe_url = redact_ws_owner_token(url)

            try:
                logger.info("wake_loop.device_wake_connecting", url=safe_url)
                async with websockets.connect(
                    url, open_timeout=5, close_timeout=2, ping_interval=None
                ) as ws:
                    self._server.esp32.mark_active_healthy()
                    logger.info("wake_loop.device_wake_connected", url=safe_url)
                    delay = 1.0
                    while self._running and self._capture_is_esp32 and not self._device_wake_paused():
                        try:
                            message = await asyncio.wait_for(
                                ws.recv(), timeout=DEVICE_WAKE_IDLE_LOG_S
                            )
                        except asyncio.TimeoutError:
                            logger.debug("wake_loop.device_wake_idle", url=safe_url)
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
                        break
            except asyncio.CancelledError:
                raise
            except TimeoutError as exc:
                logger.warning("wake_loop.device_wake_open_timeout", url=safe_url, error=str(exc))
            except Exception as exc:
                logger.warning(
                    "wake_loop.device_wake_error",
                    url=safe_url,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            if self._running and self._capture_is_esp32:
                await asyncio.sleep(delay)
            delay = min(delay * 1.5, 30.0)

    async def _sync_device_wake_model(self) -> None:
        """Best-effort keep ESP32 WakeNet model aligned with voice.wake_word."""
        if not (hasattr(self._server, "esp32") and self._server.esp32):
            return
        configured = str(self._server.config.voice.wake_word or "").strip()
        desired = ESP32_WAKE_MODEL_BY_CONFIG.get(configured)
        if not desired:
            return
        now = asyncio.get_running_loop().time()
        if now < self._next_wake_model_sync_at:
            return
        self._next_wake_model_sync_at = now + 30.0

        try:
            status, body, _ = await self._server.esp32.proxy_get("/device/status")
        except Exception as exc:
            logger.debug(
                "wake_loop.wake_model_status_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if status >= 400 or not isinstance(body, dict):
            logger.debug("wake_loop.wake_model_status_unavailable", status=status)
            return

        active = str(body.get("wake_model") or "")
        requested = str(body.get("wake_requested_model") or "")
        if active == desired and requested == desired:
            return

        supported = body.get("wake_supported_models") or []
        if isinstance(supported, list) and supported and desired not in {str(m) for m in supported}:
            logger.warning(
                "wake_loop.wake_model_not_advertised",
                desired=desired,
                active=active,
                requested=requested,
                supported=supported,
            )
            return

        payload = {"wake_model": desired}
        if hasattr(self._server.esp32, "with_owner_auth"):
            payload = self._server.esp32.with_owner_auth(payload, reason="wake_model_sync")
        try:
            sync_status, sync_body, _ = await self._server.esp32.proxy_post("/device/config", payload)
        except Exception as exc:
            logger.warning(
                "wake_loop.wake_model_sync_error",
                desired=desired,
                active=active,
                requested=requested,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if sync_status >= 400:
            logger.warning(
                "wake_loop.wake_model_sync_failed",
                desired=desired,
                active=active,
                requested=requested,
                status=sync_status,
                body=str(sync_body)[:200],
            )
            return
        logger.info(
            "wake_loop.wake_model_synced",
            desired=desired,
            previous_active=active,
            previous_requested=requested,
        )

    async def _handle_wake_detected(self, *, model: str, source: str) -> None:
        if self._bridge.state != ConversationState.IDLE:
            return
        if self._bridge_start_task and not self._bridge_start_task.done():
            return

        logger.info("wake_loop.wake_word_detected", model=model, source=source)
        self.pause_device_wake_listener(duration_s=60.0)
        self._ring_buffer.clear()

        from lampgo.core.events import WakeWordDetected

        await self._server.events.publish(WakeWordDetected(model=model))
        await asyncio.sleep(0)

    async def end_conversation(self) -> None:
        """Manually end the current conversation (called from frontend)."""
        if self._bridge.state in (ConversationState.ACTIVE, ConversationState.JOINING):
            logger.info("wake_loop.manual_end")
            await self._bridge.stop_conversation()

    async def _on_conversation_state(self, state: ConversationState) -> None:
        """Publish conversation state changes to the EventBus."""
        from lampgo.core.events import ConversationStateChanged

        try:
            await self._server.events.publish(
                ConversationStateChanged(state=state.value)
            )
        except Exception:
            logger.debug("wake_loop.event_publish_error", exc_info=True)
