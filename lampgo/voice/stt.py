"""STT — Speech-to-Text via OpenAI Whisper API."""

from __future__ import annotations

import base64
import io
import wave

import structlog

logger = structlog.get_logger(__name__)


class WhisperSTT:
    """Transcribe audio bytes using the OpenAI Whisper API."""

    def __init__(
        self,
        api_key: str,
        api_base: str = "",
        model: str = "whisper-1",
        fallback_chat_model: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_base = api_base or "https://api.openai.com/v1"
        self._model = model
        self._fallback_chat_model = fallback_chat_model

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

        transcribe_url = f"{self._api_base}/audio/transcriptions"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    transcribe_url,
                    headers=headers,
                    files=files,
                    data=data,
                )
                resp.raise_for_status()
                result = resp.json()
                text = result.get("text", "").strip()
                logger.info("stt.transcribed", text=text[:50])
                return text
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            # Some OpenAI-compatible gateways do not implement /audio/transcriptions.
            if status in (404, 405, 501):
                logger.warning(
                    "stt.transcribe_endpoint_unsupported",
                    url=transcribe_url,
                    status=status,
                    fallback="chat_completions_audio",
                )
                return await self._transcribe_via_chat_completions(httpx, headers, wav_buf)
            logger.warning("stt.transcribe_http_error", status=status, url=transcribe_url)
            return ""
        except Exception:
            logger.warning("stt.transcribe_failed")
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

    async def _transcribe_via_chat_completions(
        self,
        httpx_mod,
        headers: dict[str, str],
        wav_buf: bytes,
    ) -> str:
        """Fallback STT using chat/completions audio input for omni models."""
        model = self._fallback_chat_model or "gpt-4o-mini-transcribe"
        b64_audio = base64.b64encode(wav_buf).decode("ascii")
        body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a speech recognizer. Transcribe Chinese speech faithfully and return plain text only.",
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
            "max_tokens": 256,
        }

        try:
            async with httpx_mod.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._api_base}/chat/completions",
                    headers={**headers, "Content-Type": "application/json"},
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.warning("stt.fallback_chat_failed")
            return ""

        choices = data.get("choices", [])
        if not choices:
            return ""

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    maybe = item.get("text")
                    if isinstance(maybe, str) and maybe.strip():
                        text_parts.append(maybe.strip())
            text = " ".join(text_parts).strip()
        else:
            text = ""

        if text:
            logger.info("stt.transcribed_fallback", text=text[:50], model=model)
        return text
