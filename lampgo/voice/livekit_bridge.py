"""LiveKit room bridge — join / publish ESP32 audio / leave.

Manages the lifecycle of a LiveKit room session:
  1. Generate an access token via ``livekit-api``.
  2. Connect to the room and publish a local audio track fed from the
     ESP32 PCM stream.
  3. Optionally flush a ring-buffer of recent audio captured before the
     wake word was detected (back-fill).
  4. Continuously forward incoming PCM chunks to the LiveKit audio track.
  5. Subscribe to the agent's TTS audio track published in the same room
     and play it back through the local speaker (sounddevice).
  6. Disconnect on conversation end (silence timeout / goodbye / manual).

The Lampgo LiveKit Agent SDK, running as a separate process, subscribes
to the published microphone audio in the same room, pipes it through
Volcengine ASR, sends the text to lampgo's ``/v1/chat/completions``
endpoint, synthesizes the LLM reply via Volcengine TTS, and finally
publishes the TTS audio back into the same room as a remote track.
We subscribe to that remote track and play it through the local speaker.
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections import deque
from typing import TYPE_CHECKING

import httpx
import numpy as np
import structlog

if TYPE_CHECKING:
    from lampgo.core.config import VoiceConfig

logger = structlog.get_logger(__name__)

SAMPLE_RATE = 16000
NUM_CHANNELS = 1
SAMPLES_PER_CHANNEL = 480  # 30ms at 16 kHz — matches ESP32 chunk size

# Match the SDK roles.yaml / Volcengine TTS output sample rate.
# The Lampgo LiveKit Agent SDK config uses tts.sample_rate=24000, so keep
# the local pull stream and speaker playback at 24 kHz rather than forcing a
# 48 kHz experiment that can make PortAudio output silently on some devices.
PLAYBACK_SAMPLE_RATE = 24000
PLAYBACK_NUM_CHANNELS = 1
PLAYBACK_PREBUFFER_MS = 120


class ConversationState(str, enum.Enum):
    IDLE = "idle"
    JOINING = "joining"
    ACTIVE = "active"
    LEAVING = "leaving"


class LiveKitBridge:
    """Manages a single LiveKit room session for voice conversation."""

    def __init__(self, config: VoiceConfig, agent_sdk_port: int = 18790) -> None:
        self._config = config
        self._agent_sdk_port = agent_sdk_port
        self._state = ConversationState.IDLE
        self._room = None
        self._audio_source = None
        self._audio_track = None
        self._forward_task: asyncio.Task | None = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1000)
        self._last_audio_time: float = 0.0
        self._last_user_voice_time: float = 0.0
        self._silence_task: asyncio.Task | None = None
        self._on_state_change = None
        self._stop_event = asyncio.Event()
        # Playback of the remote TTS audio track published by the agent.
        self._playback = None  # lampgo.voice.audio.AudioPlayback
        self._remote_audio_streams: dict[str, object] = {}
        self._remote_audio_tasks: dict[str, asyncio.Task] = {}
        self._remote_audio_frame_count = 0
        self._last_remote_audio_frame_time: float | None = None
        self._drop_history_once = False
        self._queued_audio_chunks = 0
        self._forwarded_audio_frames = 0

    @property
    def state(self) -> ConversationState:
        return self._state

    def set_state_callback(self, callback) -> None:
        """Register ``async def callback(state: ConversationState)``."""
        self._on_state_change = callback

    async def _set_state(self, new_state: ConversationState) -> None:
        old = self._state
        self._state = new_state
        logger.info("livekit.state_change", old=old.value, new=new_state.value)
        if self._on_state_change:
            try:
                await self._on_state_change(new_state)
            except Exception:
                logger.exception("livekit.state_callback_error")

    # -- public API ------------------------------------------------------------

    async def start_conversation(self, backfill: deque[bytes] | None = None) -> bool:
        """Join the LiveKit room and start publishing audio.

        *backfill* is an optional deque of recent PCM chunks captured
        before the wake word was detected, flushed into the room
        immediately after the track is published.
        """
        if self._state != ConversationState.IDLE:
            logger.warning("livekit.start_ignored", state=self._state.value)
            return False

        await self._set_state(ConversationState.JOINING)
        # A previous stop_conversation() leaves this set. Clear it before
        # connecting because remote tracks can subscribe during room.connect().
        self._stop_event.clear()
        self._remote_audio_frame_count = 0
        self._last_remote_audio_frame_time = None
        self._drop_history_once = True
        self._queued_audio_chunks = 0
        self._forwarded_audio_frames = 0

        try:
            from livekit import rtc

            voice_agent = "lampgo-jarvis"
            room_name = self._config.livekit_room or "lampgo"
            identity = "lampgo-mic"

            token_url = f"http://127.0.0.1:{self._agent_sdk_port}/rtc/token"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(token_url, json={
                    "room_name": room_name,
                    "user_identity": identity,
                    "voice_agent": voice_agent,
                })
                resp.raise_for_status()
                token_data = resp.json()
            jwt = token_data["token"]
            server_url = token_data.get("serverUrl", self._config.livekit_url)
            logger.info(
                "livekit.token_acquired",
                room=token_data.get("roomName", room_name),
                agent=voice_agent,
            )

            self._room = rtc.Room()

            # Register remote-track listeners BEFORE connecting so we
            # don't miss the agent's TTS track if it publishes early.
            self._register_remote_track_handlers(self._room)

            await self._room.connect(server_url, jwt)
            logger.info("livekit.room_connected", room=room_name)

            # Start the local speaker playback pipeline. Frames pulled
            # from the agent's remote audio track will be fed in here.
            self._start_playback()

            self._audio_source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)
            self._audio_track = rtc.LocalAudioTrack.create_audio_track(
                "esp32-mic", self._audio_source
            )
            publish_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await self._room.local_participant.publish_track(
                self._audio_track, publish_options
            )
            logger.info("livekit.track_published")

            if backfill:
                for chunk in backfill:
                    await self._push_audio_frame(chunk)
                logger.info("livekit.backfill_flushed", chunks=len(backfill))

            self._last_audio_time = time.monotonic()
            self._last_user_voice_time = self._last_audio_time
            self._forward_task = asyncio.create_task(self._audio_forward_loop())
            self._silence_task = asyncio.create_task(self._silence_watchdog())

            await self._set_state(ConversationState.ACTIVE)
            return True

        except Exception:
            logger.exception("livekit.start_failed")
            await self._cleanup()
            await self._set_state(ConversationState.IDLE)
            return False

    async def stop_conversation(self) -> None:
        """Leave the LiveKit room and return to idle."""
        if self._state == ConversationState.IDLE:
            return
        await self._set_state(ConversationState.LEAVING)
        self._stop_event.set()
        await self._cleanup()
        await self._set_state(ConversationState.IDLE)

    def feed_audio(self, pcm_chunk: bytes) -> None:
        """Enqueue a PCM chunk for forwarding into the LiveKit room.

        Called from the WakeLoop audio pipeline; must be non-blocking.
        """
        if self._state not in (ConversationState.JOINING, ConversationState.ACTIVE):
            return
        try:
            self._audio_queue.put_nowait(pcm_chunk)
            self._queued_audio_chunks += 1
            if self._queued_audio_chunks == 1 or self._queued_audio_chunks % 100 == 0:
                logger.info(
                    "livekit.audio_chunk_queued",
                    chunks=self._queued_audio_chunks,
                    queue_size=self._audio_queue.qsize(),
                    state=self._state.value,
                )
        except asyncio.QueueFull:
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._audio_queue.put_nowait(pcm_chunk)
            self._queued_audio_chunks += 1
            logger.warning(
                "livekit.audio_queue_full_drop_oldest",
                chunks=self._queued_audio_chunks,
                queue_size=self._audio_queue.qsize(),
                state=self._state.value,
            )

    def mark_user_voice_activity(self) -> None:
        """Record that ASR delivered a user utterance for this conversation."""
        if self._state == ConversationState.ACTIVE:
            self._last_user_voice_time = time.monotonic()

    def consume_drop_history_once(self) -> bool:
        """Return whether the next SDK request should ignore carried history."""
        should_drop = self._drop_history_once
        self._drop_history_once = False
        return should_drop

    def check_goodbye(self, response_text: str) -> bool:
        """Return True if the LLM response contains a farewell phrase."""
        goodbye_phrases = [
            "再见", "拜拜", "goodbye", "bye", "晚安", "下次见",
            "回头见", "告辞", "see you",
        ]
        lower = response_text.lower()
        return any(phrase in lower for phrase in goodbye_phrases)

    async def wait_for_remote_playout(
        self,
        *,
        first_frame_timeout_s: float = 6.0,
        idle_s: float = 0.8,
        max_wait_s: float = 20.0,
    ) -> bool:
        """Wait until the agent's remote TTS audio has played out locally.

        This watches the LiveKit remote audio frames we receive from the SDK and
        the local jitter buffer. It returns True once at least one relevant frame
        was observed and the stream has been quiet for ``idle_s`` with no queued
        playback bytes left. It returns False on timeout, so callers can still
        hang up as a fallback.
        """
        started = False
        baseline_frames = self._remote_audio_frame_count
        start = time.monotonic()
        deadline = start + max_wait_s
        first_frame_deadline = start + first_frame_timeout_s

        while time.monotonic() < deadline:
            now = time.monotonic()
            last_frame = self._last_remote_audio_frame_time
            playback_idle = True
            buffered_s = 0.0
            if self._playback is not None:
                is_idle = getattr(self._playback, "is_idle", None)
                buffered_duration_s = getattr(self._playback, "buffered_duration_s", None)
                if callable(is_idle):
                    playback_idle = bool(is_idle())
                if callable(buffered_duration_s):
                    buffered_s = float(buffered_duration_s())

            if not started:
                has_new_frames = self._remote_audio_frame_count > baseline_frames
                recently_received = last_frame is not None and (now - last_frame) < idle_s
                if has_new_frames or recently_received or not playback_idle:
                    started = True
                elif now >= first_frame_deadline:
                    logger.warning(
                        "livekit.remote_playout_wait_no_audio",
                        waited_s=f"{now - start:.1f}",
                    )
                    return False
                await asyncio.sleep(0.05)
                continue

            quiet = last_frame is None or (now - last_frame) >= idle_s
            if quiet and playback_idle:
                logger.info(
                    "livekit.remote_playout_finished",
                    waited_s=f"{now - start:.1f}",
                    buffered_s=f"{buffered_s:.2f}",
                    frames=self._remote_audio_frame_count - baseline_frames,
                )
                return True
            await asyncio.sleep(0.05)

        logger.warning(
            "livekit.remote_playout_wait_timeout",
            waited_s=f"{max_wait_s:.1f}",
        )
        return False

    # -- internal --------------------------------------------------------------

    def _start_playback(self) -> None:
        """Initialise the local speaker pipeline for the agent's TTS audio."""
        if self._playback is not None:
            return
        try:
            from lampgo.voice.audio import JitterBufferPlayback

            self._playback = JitterBufferPlayback(
                sample_rate=PLAYBACK_SAMPLE_RATE,
                channels=PLAYBACK_NUM_CHANNELS,
                prebuffer_ms=PLAYBACK_PREBUFFER_MS,
            )
            self._playback.start()
            logger.info(
                "livekit.playback_started",
                sample_rate=PLAYBACK_SAMPLE_RATE,
                channels=PLAYBACK_NUM_CHANNELS,
                prebuffer_ms=PLAYBACK_PREBUFFER_MS,
            )
        except Exception:
            logger.exception("livekit.playback_start_failed")
            self._playback = None

    def _register_remote_track_handlers(self, room) -> None:
        """Subscribe to the agent's remote audio track when it appears."""
        from livekit import rtc

        @room.on("track_subscribed")
        def _on_track_subscribed(track, publication, participant):
            try:
                kind = getattr(track, "kind", None)
                if kind != rtc.TrackKind.KIND_AUDIO:
                    return
                logger.info(
                    "livekit.remote_audio_track_subscribed",
                    participant=getattr(participant, "identity", "?"),
                    track_sid=getattr(publication, "sid", "?"),
                )
                sid = getattr(publication, "sid", None) or str(id(track))
                task = asyncio.create_task(self._consume_remote_audio(track, sid))
                self._remote_audio_tasks[sid] = task
            except Exception:
                logger.exception("livekit.remote_audio_track_handler_failed")

        @room.on("track_unsubscribed")
        def _on_track_unsubscribed(track, publication, participant):
            sid = getattr(publication, "sid", None) or str(id(track))
            stream = self._remote_audio_streams.pop(sid, None)
            task = self._remote_audio_tasks.pop(sid, None)
            if stream is not None:
                try:
                    asyncio.create_task(stream.aclose())
                except Exception:
                    logger.debug("livekit.remote_stream_close_error", exc_info=True)
            if task is not None and not task.done():
                task.cancel()
            logger.info(
                "livekit.remote_audio_track_unsubscribed",
                participant=getattr(participant, "identity", "?"),
                track_sid=sid,
            )

    async def _consume_remote_audio(self, track, sid: str | None = None) -> None:
        """Pull frames from a remote audio track and feed them to the speaker."""
        try:
            from livekit import rtc

            stream = rtc.AudioStream.from_track(
                track=track,
                sample_rate=PLAYBACK_SAMPLE_RATE,
                num_channels=PLAYBACK_NUM_CHANNELS,
            )
        except Exception:
            logger.exception("livekit.remote_audio_stream_init_failed")
            return

        sid = sid or str(id(track))
        self._remote_audio_streams[sid] = stream

        frames_played = 0
        try:
            async for event in stream:
                if self._stop_event.is_set():
                    break
                if self._playback is None:
                    continue
                frame = event.frame
                try:
                    pcm_bytes = bytes(frame.data)
                except Exception:
                    logger.debug("livekit.remote_frame_bytes_error", exc_info=True)
                    continue
                self._playback.feed(pcm_bytes)
                frames_played += 1
                self._remote_audio_frame_count += 1
                self._last_remote_audio_frame_time = time.monotonic()
                if frames_played == 1:
                    logger.info("livekit.remote_audio_first_frame")
                self._last_audio_time = time.monotonic()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("livekit.remote_audio_consume_error")
        finally:
            self._remote_audio_streams.pop(sid, None)
            try:
                await stream.aclose()
            except Exception:
                logger.debug("livekit.remote_stream_close_error", exc_info=True)
            logger.info("livekit.remote_audio_stream_closed", frames=frames_played)

    async def _audio_forward_loop(self) -> None:
        """Continuously drain the audio queue and push frames to LiveKit."""
        logger.info("livekit.forward_loop_started", queue_size=self._audio_queue.qsize())
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = await asyncio.wait_for(
                        self._audio_queue.get(), timeout=0.1
                    )
                    await self._push_audio_frame(chunk)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("livekit.forward_loop_error")

    async def _push_audio_frame(self, pcm_chunk: bytes) -> None:
        """Convert raw PCM16LE bytes to a LiveKit AudioFrame and capture it."""
        if self._audio_source is None:
            return
        try:
            from livekit import rtc

            samples = np.frombuffer(pcm_chunk, dtype=np.int16)
            num_samples = len(samples)
            frame = rtc.AudioFrame(
                data=samples.tobytes(),
                sample_rate=SAMPLE_RATE,
                num_channels=NUM_CHANNELS,
                samples_per_channel=num_samples,
            )
            await self._audio_source.capture_frame(frame)
            self._forwarded_audio_frames += 1
            if self._forwarded_audio_frames == 1 or self._forwarded_audio_frames % 100 == 0:
                rms = 0.0
                peak = 0
                if num_samples:
                    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                    peak = int(np.max(np.abs(samples)))
                logger.info(
                    "livekit.audio_frame_forwarded",
                    frames=self._forwarded_audio_frames,
                    samples=num_samples,
                    queue_size=self._audio_queue.qsize(),
                    rms=f"{rms:.1f}",
                    peak=peak,
                )
        except Exception:
            logger.debug("livekit.push_frame_error", exc_info=True)

    async def _silence_watchdog(self) -> None:
        """Monitor for no user speech transcript and auto-end the conversation."""
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(5.0)
                timeout = self._config.silence_timeout_s
                elapsed = time.monotonic() - self._last_user_voice_time
                if elapsed >= timeout:
                    logger.info(
                        "livekit.silence_timeout",
                        elapsed_s=f"{elapsed:.0f}",
                        timeout_s=timeout,
                    )
                    await self.stop_conversation()
                    return
        except asyncio.CancelledError:
            pass

    async def _cleanup(self) -> None:
        """Cancel tasks and disconnect from the room."""
        if self._forward_task and not self._forward_task.done():
            self._forward_task.cancel()
            try:
                await self._forward_task
            except asyncio.CancelledError:
                pass
            self._forward_task = None

        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
            try:
                await self._silence_task
            except asyncio.CancelledError:
                pass
            self._silence_task = None

        for sid, task in list(self._remote_audio_tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug("livekit.remote_task_cancel_error", exc_info=True)
        self._remote_audio_tasks.clear()

        for sid, stream in list(self._remote_audio_streams.items()):
            try:
                await stream.aclose()
            except Exception:
                logger.debug("livekit.remote_stream_close_error", exc_info=True)
        self._remote_audio_streams.clear()

        if self._room:
            try:
                await self._room.disconnect()
                logger.info("livekit.room_disconnected")
            except Exception:
                logger.debug("livekit.disconnect_error", exc_info=True)
            self._room = None

        if self._playback is not None:
            try:
                self._playback.stop()
            except Exception:
                logger.debug("livekit.playback_stop_error", exc_info=True)
            self._playback = None

        self._audio_source = None
        self._audio_track = None

        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
