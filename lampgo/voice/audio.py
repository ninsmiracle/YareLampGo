"""Audio I/O — microphone capture and speaker playback using sounddevice."""

from __future__ import annotations

import asyncio
import queue

import structlog

logger = structlog.get_logger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
CHUNK_DURATION_MS = 30
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)


class AudioCapture:
    """Non-blocking microphone capture using sounddevice."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, chunk_ms: int = CHUNK_DURATION_MS) -> None:
        self._sample_rate = sample_rate
        self._chunk_samples = int(sample_rate * chunk_ms / 1000)
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=500)
        self._stream = None

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            logger.warning("audio.no_sounddevice", msg="Install sounddevice: uv add sounddevice")
            return

        def callback(indata, frames, time_info, status):
            if status:
                logger.debug("audio.status", status=str(status))
            self._queue.put(bytes(indata))

        self._stream = sd.RawInputStream(
            samplerate=self._sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=self._chunk_samples,
            callback=callback,
        )
        self._stream.start()
        logger.info("audio.capture_started", rate=self._sample_rate)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def read_chunk(self, timeout: float = 0.1) -> bytes | None:
        """Read one audio chunk. Returns None if no data available."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    async def aread_chunk(self, timeout: float = 0.1) -> bytes | None:
        """Async wrapper around read_chunk."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.read_chunk, timeout)
