"""STT — Speech-to-Text via MiMo-V2-Omni (primary) or Whisper API (fallback)."""

from __future__ import annotations

import base64
import io
import wave

import structlog

logger = structlog.get_logger(__name__)


class OmniSTT:
    """Transcribe audio using chat completions with input_audio (MiMo-V2-Omni).

    Works with any OpenAI-compatible API that supports audio content parts.
    Preserves tone and emotion context that text-only transcription loses.
    """

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.mimomimo.com/v1",
        model: str = "mimo-v2-omni",
    ) -> None:
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._model = model

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcribe raw PCM 16-bit mono audio to text via omni model."""
        wav_buf = _pcm_to_wav(audio_bytes, sample_rate)

        try:
            import httpx
        except ImportError:
            logger.warning("stt.no_httpx")
            return ""

        b64_audio = base64.b64encode(wav_buf).decode("ascii")
        body = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是语音转写器。忠实转写中文语音，只返回纯文字，不加标点解释。",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请转写这段语音，只返回文字。"},
                        {"type": "input_audio", "input_audio": {"data": b64_audio, "format": "wav"}},
                    ],
                },
            ],
            "temperature": 0,
            "max_completion_tokens": 256,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._api_base}/chat/completions",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("stt.omni_request_failed")
            return ""

        text = _extract_text(data)
        if text:
            logger.info("stt.transcribed", text=text[:50], model=self._model)
        return text


class WhisperSTT:
    """Transcribe audio using the OpenAI Whisper /audio/transcriptions endpoint."""

    def __init__(
        self,
        api_key: str,
        api_base: str = "",
        model: str = "whisper-1",
    ) -> None:
        self._api_key = api_key
        self._api_base = api_base or "https://api.openai.com/v1"
        self._model = model

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        wav_buf = _pcm_to_wav(audio_bytes, sample_rate)

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
                if text:
                    logger.info("stt.transcribed", text=text[:50])
                return text
        except Exception:
            logger.exception("stt.whisper_request_failed")
            return ""


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _extract_text(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
        return " ".join(parts).strip()
    return ""
