"""Audio I/O — microphone capture and speaker playback using sounddevice."""

from __future__ import annotations

import asyncio
import queue
import threading

import structlog

logger = structlog.get_logger(__name__)

CAPTURE_SAMPLE_RATE = 16000
PLAYBACK_SAMPLE_RATE = 24000
CHANNELS = 1
DTYPE = "int16"
CHUNK_DURATION_MS = 30
CHUNK_SAMPLES = int(CAPTURE_SAMPLE_RATE * CHUNK_DURATION_MS / 1000)


class AudioCapture:
    """Non-blocking microphone capture using sounddevice."""

    def __init__(
        self,
        sample_rate: int = CAPTURE_SAMPLE_RATE,
        chunk_ms: int = CHUNK_DURATION_MS,
        device: int | str | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._chunk_samples = int(sample_rate * chunk_ms / 1000)
        self._device = device
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

        dev = self._device
        dev_info = None
        if dev is not None:
            try:
                dev_info = sd.query_devices(dev)
            except Exception:
                logger.warning("audio.device_not_found", device=dev, msg="Falling back to system default")
                dev = None

        self._stream = sd.RawInputStream(
            samplerate=self._sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=self._chunk_samples,
            device=dev,
            callback=callback,
        )
        self._stream.start()
        dev_name = dev_info["name"] if dev_info else "system default"
        logger.info("audio.capture_started", rate=self._sample_rate, device=dev, device_name=dev_name)

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


class AudioPlayback:
    """Real-time PCM playback via sounddevice output stream.

    Accepts PCM16LE mono chunks (24kHz by default, matching MiMo TTS output)
    and plays them immediately through the system speaker.
    """

    def __init__(self, sample_rate: int = PLAYBACK_SAMPLE_RATE) -> None:
        self._sample_rate = sample_rate
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=200)
        self._stream = None
        self._finished = threading.Event()

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            logger.warning("audio.no_sounddevice")
            return

        def callback(outdata, frames, time_info, status):
            bytes_needed = frames * 2  # int16 = 2 bytes per sample
            data = b""
            while len(data) < bytes_needed:
                try:
                    chunk = self._queue.get_nowait()
                except queue.Empty:
                    break
                if chunk is None:
                    self._finished.set()
                    break
                data += chunk

            if len(data) >= bytes_needed:
                outdata[:] = memoryview(data[:bytes_needed]).cast("B")
                if len(data) > bytes_needed:
                    self._queue.put(data[bytes_needed:])
            else:
                outdata[:len(data)] = memoryview(data).cast("B")
                outdata[len(data):] = b"\x00" * (bytes_needed - len(data))

        self._finished.clear()
        self._stream = sd.RawOutputStream(
            samplerate=self._sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=1024,
            callback=callback,
        )
        self._stream.start()
        logger.debug("audio.playback_started", rate=self._sample_rate)

    def feed(self, pcm_bytes: bytes) -> None:
        """Push a PCM chunk into the playback buffer."""
        try:
            self._queue.put_nowait(pcm_bytes)
        except queue.Full:
            logger.debug("audio.playback_queue_full")

    def finish(self) -> None:
        """Signal that no more audio will be fed."""
        self._queue.put(None)

    def wait_done(self, timeout: float = 30.0) -> None:
        """Block until all queued audio has been played."""
        self._finished.wait(timeout=timeout)

    async def await_done(self, timeout: float = 30.0) -> None:
        """Async version of wait_done."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.wait_done, timeout)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def drain_and_stop(self) -> None:
        """Wait for playback to finish then close the stream."""
        self.finish()
        self.wait_done()
        self.stop()


class JitterBufferPlayback:
    """Speaker playback for *bursty / jittered* PCM streams (e.g. LiveKit).

    Differences vs :class:`AudioPlayback` (which is tuned for lampgo's own
    synchronous streaming TTS, where chunks arrive at a steady cadence):

    * Buffers ``prebuffer_ms`` worth of audio before the first sample is
      played, absorbing network jitter and the initial Volcengine TTS
      ramp-up.
    * Stores accumulated PCM in a single ``bytearray`` under a lock, so an
      sounddevice ``callback`` consuming arbitrary block sizes never
      fragments individual upstream frames.
    * On underrun (queue empties mid-playback), pads the current callback
      with silence **and re-enters prebuffering mode** so we don't ship a
      stream of clicks/pops when the source recovers.
    * ``blocksize=0`` lets sounddevice/PortAudio pick the optimal callback
      size for the device.

    PCM input must be int16 little-endian mono / multi-channel matching
    the configured ``sample_rate`` × ``channels``.
    """

    def __init__(
        self,
        sample_rate: int,
        channels: int = 1,
        *,
        prebuffer_ms: int = 200,
        max_buffer_ms: int = 4000,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._bytes_per_sample = 2  # int16
        self._prebuffer_bytes = (
            sample_rate * channels * self._bytes_per_sample * prebuffer_ms // 1000
        )
        self._max_buffer_bytes = (
            sample_rate * channels * self._bytes_per_sample * max_buffer_ms // 1000
        )
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stream = None
        self._playing = False  # True once prebuffer threshold reached

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            logger.warning("audio.no_sounddevice")
            return

        def callback(outdata, frames, time_info, status):
            bytes_needed = frames * self._channels * self._bytes_per_sample
            with self._lock:
                if not self._playing:
                    if len(self._buffer) >= self._prebuffer_bytes:
                        self._playing = True
                    else:
                        outdata[:] = memoryview(b"\x00" * bytes_needed).cast("B")
                        return

                if len(self._buffer) >= bytes_needed:
                    chunk = bytes(self._buffer[:bytes_needed])
                    outdata[:] = memoryview(chunk).cast("B")
                    del self._buffer[:bytes_needed]
                else:
                    have = len(self._buffer)
                    if have:
                        chunk = bytes(self._buffer)
                        outdata[:have] = memoryview(chunk).cast("B")
                    outdata[have:] = memoryview(b"\x00" * (bytes_needed - have)).cast("B")
                    self._buffer.clear()
                    self._playing = False  # re-arm prebuffer

        blocksize = int(self._sample_rate * 0.02)  # 20 ms, matches LiveKit audio frames
        self._stream = sd.RawOutputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype=DTYPE,
            blocksize=blocksize,
            latency="low",
            callback=callback,
        )
        self._stream.start()
        logger.debug(
            "audio.jitter_playback_started",
            sample_rate=self._sample_rate,
            channels=self._channels,
            prebuffer_ms=self._prebuffer_bytes
            * 1000
            // (self._sample_rate * self._channels * self._bytes_per_sample),
        )

    def feed(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            return
        with self._lock:
            self._buffer.extend(pcm_bytes)
            if len(self._buffer) > self._max_buffer_bytes:
                drop = len(self._buffer) - self._max_buffer_bytes
                del self._buffer[:drop]
                logger.debug("audio.jitter_buffer_overflow", dropped_bytes=drop)

    def buffered_duration_s(self) -> float:
        """Return the approximate amount of queued, not-yet-played audio."""
        with self._lock:
            return len(self._buffer) / (
                self._sample_rate * self._channels * self._bytes_per_sample
            )

    def is_idle(self) -> bool:
        """Return True when there is no buffered audio waiting for playback."""
        with self._lock:
            return not self._playing and not self._buffer

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.debug("audio.jitter_playback_stop_error", exc_info=True)
            self._stream = None
        with self._lock:
            self._buffer.clear()
            self._playing = False
