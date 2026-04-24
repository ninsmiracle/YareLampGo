"""STT — Speech-to-Text via MiMo chat completions (input_audio)."""

from __future__ import annotations

import base64
import io
import wave
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from lampgo.core.config import LampgoConfig

logger = structlog.get_logger(__name__)

MIMO_OPENAI_BASE = "https://api.mimomimo.com/v1"
DEFAULT_MODEL = "mimo-v2.5"


# ---------------------------------------------------------------------------
# MiMo STT — unified for mimo-v2.5 / mimo-v2-omni / any compatible model
# ---------------------------------------------------------------------------


class MimoSTT:
    """Transcribe audio via MiMo chat completions with ``input_audio``.

    Works with any model that supports the ``input_audio`` content part
    on the ``/v1/chat/completions`` endpoint (e.g. mimo-v2.5, mimo-v2-omni).
    """

    def __init__(
        self,
        api_key: str,
        api_base: str = MIMO_OPENAI_BASE,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._model = model

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcribe raw PCM 16-bit mono audio → text."""
        wav_buf = _pcm_to_wav(audio_bytes, sample_rate)
        b64_audio = base64.b64encode(wav_buf).decode("ascii")
        return await self._call_api(b64_audio)

    async def transcribe_wav_b64(self, wav_b64: str) -> str:
        """Transcribe base64-encoded WAV → text."""
        return await self._call_api(wav_b64)

    async def _call_api(self, b64_audio: str) -> str:
        try:
            import httpx
        except ImportError:
            logger.warning("stt.no_httpx")
            return ""

        body = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是语音转写助手。忠实转写用户语音，只输出用户说的原文，"
                        "不要添加任何解释、标点修饰或描述。如果听不清就输出空字符串。"
                        "保持与说话者相同的语言。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": b64_audio, "format": "wav"}},
                        {"type": "text", "text": "请转写这段语音，只返回文字。"},
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

        logger.info("stt.calling_api", model=self._model, audio_b64_len=len(b64_audio))
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self._api_base}/chat/completions",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("stt.request_failed", model=self._model)
            return ""

        text = _extract_text(data)
        if text:
            logger.info("stt.transcribed", text=text[:80], model=self._model)
        else:
            raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            logger.warning("stt.empty_result", model=self._model, raw_content=str(raw)[:200])
        return text


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _resolve_mimo_base(raw_base: str) -> str:
    """MiMo STT always needs the OpenAI-compatible endpoint, never /anthropic/."""
    if "mimomimo.com" in raw_base:
        return MIMO_OPENAI_BASE
    return raw_base


def build_stt(config: LampgoConfig) -> MimoSTT:
    """Construct the STT backend from voice config."""
    api_key = config.llm.api_key
    api_base = _resolve_mimo_base(config.llm.api_base)
    model = config.voice.stt_model or DEFAULT_MODEL
    logger.info("stt.init", model=model, api_base=api_base)
    return MimoSTT(api_key=api_key, api_base=api_base, model=model)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
