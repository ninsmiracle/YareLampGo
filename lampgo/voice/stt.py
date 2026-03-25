"""STT — Speech-to-Text via OpenAI Whisper API."""

from __future__ import annotations

import io
import wave

import structlog

logger = structlog.get_logger(__name__)


class WhisperSTT:
    """Transcribe audio bytes using the OpenAI Whisper API."""

    def __init__(self, api_key: str, api_base: str = "", model: str = "whisper-1") -> None:
        self._api_key = api_key
        self._api_base = api_base or "https://api.openai.com/v1"
        self._model = model

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcribe raw PCM 16-bit mono audio to text."""
        wav_buf = self._pcm_to_wav(audio_bytes, sample_rate)

        try:
            import httpx
        except ImportError:
            logger.warning("stt.no_httpx")
            return ""

        headers = {"Authorization": f"Bearer {self._api_key}"}
        files = {"file": ("audio.wav", wav_buf, "audio/wav")}
        data = {"model": self._model, "language": "zh"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._api_base}/audio/transcriptions",
                    headers=headers,
                    files=files,
                    data=data,
                )
                resp.raise_for_status()
                result = resp.json()
                text = result.get("text", "").strip()
                logger.info("stt.transcribed", text=text[:50])
                return text
        except Exception:
            logger.exception("stt.transcribe_failed")
            return ""

    @staticmethod
    def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return buf.getvalue()
