"""ESP32 PDM microphone → WebSocket → PCM queue.

Connects to ``ws://{host}/ws/audio`` on the ESP32 and receives raw PCM16LE
mono 16 kHz binary frames (30 ms chunks = 960 bytes each).

Exposes the same ``start / stop / read_chunk / aread_chunk`` interface as
:class:`~lampgo.voice.audio.AudioCapture` so it can be used as a drop-in
replacement inside :class:`~lampgo.voice.loop.VoiceLoop`.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from lampgo.device.esp32 import Esp32DeviceManager

logger = structlog.get_logger(__name__)

RECONNECT_DELAY_S = 3.0
MAX_RECONNECT_DELAY_S = 30.0
WS_RECV_TIMEOUT_S = 5.0
STALE_AUDIO_S = 10.0


class Esp32AudioCapture:
    """Receive PCM audio from an ESP32 via WebSocket.

    API-compatible with :class:`~lampgo.voice.audio.AudioCapture`.
    """

    def __init__(self, esp32_manager: Esp32DeviceManager) -> None:
        self._esp32 = esp32_manager
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=500)
        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws_task: asyncio.Task | None = None
        self._connected = False
        self._last_frame_at = 0.0

    # -- public interface (matches AudioCapture) ----------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_thread, daemon=True, name="esp32-audio")
        self._thread.start()
        logger.info("esp32_audio.capture_started")

    def stop(self) -> None:
        self._running = False
        if self._loop is not None and self._ws_task is not None:
            self._loop.call_soon_threadsafe(self._ws_task.cancel)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("esp32_audio.capture_stopped")

    def read_chunk(self, timeout: float = 0.1) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    async def aread_chunk(self, timeout: float = 0.1) -> bytes | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.read_chunk, timeout)

    @property
    def is_connected(self) -> bool:
        if not (self._running and self._thread is not None and self._thread.is_alive()):
            return False
        return self._connected and (time.monotonic() - self._last_frame_at) <= STALE_AUDIO_S

    # -- internal -----------------------------------------------------------

    def _run_thread(self) -> None:
        """Background thread: run an asyncio event loop for the WS client."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._ws_task = self._loop.create_task(self._ws_loop())
            self._loop.run_until_complete(self._ws_task)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("esp32_audio.thread_crashed")
        finally:
            self._ws_task = None
            self._loop.close()
            self._loop = None

    async def _ws_loop(self) -> None:
        """Reconnecting WebSocket consumer loop."""
        delay = RECONNECT_DELAY_S
        while self._running:
            url = self._build_ws_url()
            if not url:
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, MAX_RECONNECT_DELAY_S)
                continue

            try:
                await self._connect_and_recv(url)
                delay = RECONNECT_DELAY_S
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("esp32_audio.ws_error", url=url, error=str(exc))

            if self._running:
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, MAX_RECONNECT_DELAY_S)

    async def _connect_and_recv(self, url: str) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("esp32_audio.no_websockets",
                         msg="Install websockets: uv add websockets")
            self._running = False
            return

        logger.info("esp32_audio.connecting", url=url)
        async with websockets.connect(url, open_timeout=5, close_timeout=2, ping_interval=None) as ws:
            logger.info("esp32_audio.connected", url=url)
            self._connected = True
            self._last_frame_at = time.monotonic()
            try:
                while self._running:
                    try:
                        data = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT_S)
                    except asyncio.TimeoutError:
                        if time.monotonic() - self._last_frame_at > STALE_AUDIO_S:
                            raise ConnectionError("stale ESP32 audio websocket")
                        continue
                    if isinstance(data, bytes):
                        self._last_frame_at = time.monotonic()
                        try:
                            self._queue.put_nowait(data)
                        except queue.Full:
                            try:
                                self._queue.get_nowait()
                            except queue.Empty:
                                pass
                            self._queue.put_nowait(data)
            finally:
                self._connected = False

    def _build_ws_url(self) -> str | None:
        return build_ws_audio_url(self._esp32)


def build_ws_audio_url(esp32: Esp32DeviceManager) -> str | None:
    """Build the ``ws://host:port/ws/audio`` URL for the active ESP32.

    Prefers ``dev.ip`` over ``dev.host`` to avoid flaky mDNS resolution.
    """
    if not esp32.is_online():
        return None
    dev = esp32._pick_active()
    if dev is None:
        return None
    host = dev.ip or dev.host
    port = dev.port or 80
    return f"ws://{host}:{port}/ws/audio"


def build_ws_events_url(esp32: Esp32DeviceManager) -> str | None:
    """Build the ``ws://host:port/ws/events`` URL for ESP32 device events."""
    if not esp32.is_online():
        return None
    dev = esp32._pick_active()
    if dev is None:
        return None
    host = dev.ip or dev.host
    port = dev.port or 80
    return f"ws://{host}:{port}/ws/events"


class Esp32AudioSession:
    """Stateful recording session: start → accumulate PCM → stop → get WAV.

    Used by the push-to-talk flow so the frontend controls when recording
    starts and stops (just like the browser mic).
    """

    SAMPLE_RATE = 16000
    MAX_DURATION_S = 60.0

    def __init__(self, esp32: Esp32DeviceManager) -> None:
        self._esp32 = esp32
        self._pcm = bytearray()
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._running = False

    @property
    def is_recording(self) -> bool:
        return self._running

    async def start(self) -> bool:
        """Begin recording. Returns False if device is offline."""
        url = build_ws_audio_url(self._esp32)
        if not url:
            logger.warning("esp32_audio.session_no_url")
            return False
        self._pcm.clear()
        self._stop_event.clear()
        self._running = True
        self._task = asyncio.create_task(self._record_loop(url))
        logger.info("esp32_audio.session_start", url=url)
        return True

    async def stop(self) -> bytes | None:
        """Stop recording and return WAV bytes (or None if too short)."""
        self._stop_event.set()
        self._running = False
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()
            self._task = None
        return self._build_wav()

    def cancel(self) -> None:
        """Cancel without producing output."""
        self._stop_event.set()
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self._pcm.clear()

    async def _record_loop(self, url: str) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("esp32_audio.no_websockets")
            return

        deadline = asyncio.get_event_loop().time() + self.MAX_DURATION_S
        try:
            async with websockets.connect(url, open_timeout=5, close_timeout=2, ping_interval=None) as ws:
                while not self._stop_event.is_set():
                    if asyncio.get_event_loop().time() > deadline:
                        break
                    try:
                        data = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        logger.info("esp32_audio.session_ws_closed", pcm_so_far=len(self._pcm))
                        break
                    if isinstance(data, bytes):
                        self._pcm.extend(data)
        except Exception:
            logger.info("esp32_audio.session_ws_ended", url=url, pcm_so_far=len(self._pcm))

    def _build_wav(self) -> bytes | None:
        import struct

        pcm_bytes = bytes(self._pcm)
        self._pcm.clear()
        if len(pcm_bytes) < self.SAMPLE_RATE:
            logger.warning("esp32_audio.session_too_short", bytes=len(pcm_bytes))
            return None

        data_len = len(pcm_bytes)
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_len, b"WAVE",
            b"fmt ", 16, 1, 1,
            self.SAMPLE_RATE, self.SAMPLE_RATE * 2, 2, 16,
            b"data", data_len,
        )
        logger.info("esp32_audio.session_done", pcm_bytes=data_len)
        return header + pcm_bytes
