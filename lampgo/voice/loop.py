"""VoiceLoop — continuous listen-route-act-speak cycle.

Runs as an asyncio task inside the daemon:
  [listen] -> VAD detects speech -> record segment
  -> STT (Whisper API) -> text
  -> IntentRouter -> RoutedIntent
  -> if skill: invoke + TTS confirm
  -> if chat: TTS reply
  -> back to listening
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from lampgo.voice.audio import AudioCapture
from lampgo.voice.stt import WhisperSTT
from lampgo.voice.tts import EdgeTTS
from lampgo.voice.vad import EnergyVAD

if TYPE_CHECKING:
    from lampgo.server import LampgoServer

logger = structlog.get_logger(__name__)

MAX_RECORDING_SECONDS = 10
SAMPLE_RATE = 16000


class VoiceLoop:
    """Continuous voice interaction loop."""

    def __init__(self, server: LampgoServer) -> None:
        self._server = server
        cfg = server.config
        self._stt = WhisperSTT(
            api_key=cfg.llm.api_key,
            api_base=cfg.llm.api_base,
            fallback_chat_model=cfg.llm.fast_model,
        )
        self._tts = EdgeTTS(voice=cfg.voice.tts_voice)
        self._vad = EnergyVAD()
        self._capture = AudioCapture(sample_rate=SAMPLE_RATE)
        self._running = False

    async def run(self) -> None:
        """Main voice loop. Call as an asyncio task."""
        self._capture.start()
        self._running = True
        logger.info("voice.loop_started")

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
        """One listen-respond cycle."""
        audio_data = await self._collect_speech()
        if audio_data is None or len(audio_data) < SAMPLE_RATE:  # < 0.5s
            return

        text = await self._stt.transcribe(audio_data, sample_rate=SAMPLE_RATE)
        if not text:
            return

        logger.info("voice.heard", text=text)
        response = await self._server.handle_request({"cmd": "text", "input": text})

        result = response.get("result", {})
        rtype = result.get("type", "")

        if rtype == "chat":
            reply = result.get("response", "")
            if reply:
                await self._tts.speak(reply)
        elif rtype == "skill":
            chat = result.get("chat_response", "")
            if chat:
                await self._tts.speak(chat)
        elif rtype in {"complex", "openclaw"}:
            await self._tts.speak("这个请求需要通过 OpenClaw 来处理")

    async def _collect_speech(self) -> bytes | None:
        """Listen until speech is detected, then record until silence."""
        buf = bytearray()
        speech_started = False
        max_chunks = int(MAX_RECORDING_SECONDS * SAMPLE_RATE / (SAMPLE_RATE * 0.03))

        for _ in range(max_chunks * 3):
            chunk = await self._capture.aread_chunk(timeout=0.05)
            if chunk is None:
                await asyncio.sleep(0.01)
                continue

            is_speech = self._vad.process_chunk(chunk)

            if is_speech:
                if not speech_started:
                    speech_started = True
                    logger.debug("voice.speech_started")
                buf.extend(chunk)
            elif speech_started:
                logger.debug("voice.speech_ended", bytes=len(buf))
                self._vad.reset()
                return bytes(buf)

            if not self._running:
                return None

        self._vad.reset()
        return bytes(buf) if buf else None
