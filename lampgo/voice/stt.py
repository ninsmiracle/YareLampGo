"""Speech-to-text through the MiMo model configured for LampGo's LLM."""

from __future__ import annotations

import base64
import io
import wave
from typing import TYPE_CHECKING

import structlog

from lampgo.voice.mimo import (
    MiMoAPIError,
    MiMoSpeechSettings,
    build_mimo_speech_settings,
    transcribe_mimo_wav,
)

if TYPE_CHECKING:
    from lampgo.core.config import LampgoConfig


logger = structlog.get_logger(__name__)


class MiMoASR:
    """Recognize complete PCM/WAV utterances with ``mimo-v2.5-asr``."""

    def __init__(self, settings: MiMoSpeechSettings | None, *, unavailable_reason: str = "") -> None:
        self._settings = settings
        self._unavailable_reason = unavailable_reason

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        wav_b64 = base64.b64encode(_pcm_to_wav(audio_bytes, sample_rate)).decode("ascii")
        return await self.transcribe_wav_b64(wav_b64)

    async def transcribe_wav_b64(self, wav_b64: str) -> str:
        if self._settings is None:
            logger.warning("stt.mimo_unavailable", reason=self._unavailable_reason)
            return ""
        try:
            result = await transcribe_mimo_wav(self._settings, wav_b64)
        except MiMoAPIError as exc:
            logger.warning(
                "stt.mimo_request_failed",
                model=self._settings.asr_model,
                status_code=exc.status_code,
                request_id=exc.request_id,
                error=str(exc),
            )
            return ""
        return result.text


def build_stt(config: LampgoConfig) -> MiMoASR:
    """Construct ASR from the existing MiMo LLM base URL and key."""

    try:
        settings = build_mimo_speech_settings(config.llm, config.voice)
    except ValueError as exc:
        # Keep the rest of LampGo usable for installations that have not yet
        # configured MiMo.  Voice requests are explicitly unavailable rather
        # than silently falling back to another provider.
        logger.info("stt.mimo_not_configured", reason=str(exc))
        return MiMoASR(None, unavailable_reason=str(exc))
    logger.info("stt.init", provider="mimo", model=settings.asr_model, api_base=settings.api_base)
    return MiMoASR(settings)


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
