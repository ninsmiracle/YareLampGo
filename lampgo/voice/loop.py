"""VoiceLoop — mic → VAD → audio → ASR → agent loop → TTS → speaker.

Pipeline:
  [mic] → VAD → PCM → base64 WAV
  → server.handle_request(cmd="audio") → Volcengine ASR → agent loop
  → response text → VolcengineTTS / EdgeTTS → speaker
"""

from __future__ import annotations

import asyncio
import base64
import struct
import uuid
from typing import TYPE_CHECKING

import structlog

from lampgo.voice.audio import AudioCapture, AudioPlayback
from lampgo.voice.tts import (
    DEFAULT_VOLCENGINE_TTS_VOICE,
    TTS_SAMPLE_RATE,
    EdgeTTS,
    VolcengineTTS,
)
from lampgo.voice.vad import EnergyVAD

if TYPE_CHECKING:
    from lampgo.server import LampgoServer

logger = structlog.get_logger(__name__)

MAX_RECORDING_SECONDS = 10
SAMPLE_RATE = 16000


class VoiceLoop:
    """Continuous voice interaction loop: mic → omni agent → TTS."""

    def __init__(self, server: LampgoServer) -> None:
        self._server = server
        cfg = server.config

        provider = (cfg.voice.tts_provider or "").strip().lower()
        has_volcengine_credentials = bool(cfg.voice.volcengine_app_id and cfg.voice.volcengine_access_token)
        if provider == "edge-tts" or not has_volcengine_credentials:
            self._tts: VolcengineTTS | EdgeTTS = EdgeTTS(voice=_edge_voice_or_default(cfg.voice.tts_voice))
            self._stream_tts = False
        else:
            self._tts = VolcengineTTS(
                app_id=cfg.voice.volcengine_app_id,
                access_token=cfg.voice.volcengine_access_token,
                voice=cfg.voice.tts_voice or DEFAULT_VOLCENGINE_TTS_VOICE,
                model=cfg.voice.tts_model,
            )
            self._stream_tts = True

        self._vad = EnergyVAD()
        self._capture = self._build_capture(cfg)
        self._capture_source = "esp32" if self._is_esp32_capture() else "local"
        self._running = False

    def _build_capture(self, cfg) -> AudioCapture:
        """Pick ESP32 or local mic based on config + device availability."""
        if cfg.device_esp32.mic_enabled and hasattr(self._server, "esp32") and self._server.esp32:
            esp32 = self._server.esp32
            if esp32.is_online():
                from lampgo.device.audio_stream import Esp32AudioCapture
                logger.info("voice.using_esp32_mic")
                return Esp32AudioCapture(esp32)  # type: ignore[return-value]
            logger.info("voice.esp32_mic_offline_fallback")

        mic_dev: int | str | None = None
        if cfg.voice.mic_device:
            try:
                mic_dev = int(cfg.voice.mic_device)
            except ValueError:
                mic_dev = cfg.voice.mic_device
        return AudioCapture(sample_rate=SAMPLE_RATE, device=mic_dev)

    def _is_esp32_capture(self) -> bool:
        try:
            from lampgo.device.audio_stream import Esp32AudioCapture
            return isinstance(self._capture, Esp32AudioCapture)
        except ImportError:
            return False

    async def run(self) -> None:
        self._capture.start()
        self._running = True
        logger.info("voice.loop_started", tts=type(self._tts).__name__, stream_tts=self._stream_tts)

        try:
            while self._running:
                await self._listen_and_respond()
        except asyncio.CancelledError:
            pass
        finally:
            self._capture.stop()
            self._running = False
            logger.info("voice.loop_stopped")

    def stop(self) -> None:
        self._running = False

    async def _listen_and_respond(self) -> None:
        audio_data = await self._collect_speech()
        if audio_data is None or len(audio_data) < SAMPLE_RATE:
            if audio_data and len(audio_data) > 0:
                logger.debug("voice.too_short", bytes=len(audio_data), min_bytes=SAMPLE_RATE)
            return

        audio_b64 = _pcm_to_wav_b64(audio_data, SAMPLE_RATE)
        request_id = uuid.uuid4().hex[:12]
        logger.info("voice.sending_to_agent", request_id=request_id, audio_bytes=len(audio_data))

        response = await self._server.handle_request({
            "cmd": "audio",
            "audio_data": audio_b64,
            "request_id": request_id,
        })

        result = response.get("result", {})
        reply = result.get("response") or result.get("chat_response") or ""
        if not reply:
            logger.info("voice.no_reply", request_id=request_id, result_type=result.get("type"))
            return

        logger.info("voice.speaking", request_id=request_id, reply=reply[:60])
        await self._speak(reply)

    async def _speak(self, text: str) -> None:
        if self._stream_tts and isinstance(self._tts, VolcengineTTS):
            player = AudioPlayback(sample_rate=TTS_SAMPLE_RATE)
            player.start()
            try:
                async for pcm_chunk in self._tts.stream_pcm(text):
                    player.feed(pcm_chunk)
                player.finish()
                await player.await_done(timeout=30.0)
            finally:
                player.stop()
        else:
            await self._tts.speak(text)

    async def _collect_speech(self) -> bytes | None:
        buf = bytearray()
        speech_started = False
        max_chunks = int(MAX_RECORDING_SECONDS * SAMPLE_RATE / (SAMPLE_RATE * 0.03))
        chunks_read = 0
        none_count = 0

        for _ in range(max_chunks * 3):
            chunk = await self._capture.aread_chunk(timeout=0.05)
            if chunk is None:
                none_count += 1
                if none_count % 200 == 0:
                    logger.debug("voice.no_chunks", none_count=none_count, chunks_read=chunks_read)
                await asyncio.sleep(0.01)
                continue

            chunks_read += 1
            is_speech = self._vad.process_chunk(chunk)

            if is_speech:
                if not speech_started:
                    speech_started = True
                    logger.info("voice.speech_started")
                buf.extend(chunk)
            elif speech_started:
                logger.info("voice.speech_ended", bytes=len(buf))
                self._vad.reset()
                return bytes(buf)

            if not self._running:
                return None

        logger.debug("voice.collect_timeout", chunks_read=chunks_read, buf_bytes=len(buf))
        self._vad.reset()
        return bytes(buf) if buf else None


def _pcm_to_wav_b64(pcm: bytes, sample_rate: int) -> str:
    """Wrap raw PCM16LE mono bytes into WAV format and return base64."""
    data_len = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_len, b"WAVE",
        b"fmt ", 16, 1, 1,
        sample_rate, sample_rate * 2, 2, 16,
        b"data", data_len,
    )
    return base64.b64encode(header + pcm).decode()


def _edge_voice_or_default(voice: str) -> str:
    voice = (voice or "").strip()
    return voice if "-" in voice and voice.endswith("Neural") else "zh-CN-XiaoxiaoNeural"
